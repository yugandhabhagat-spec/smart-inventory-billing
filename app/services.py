"""Business logic. All multi-step operations run in ONE transaction."""
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .extensions import db
from .models import (PAYMENT_METHODS, AuditLog, Customer, InventoryMovement, Payment,
                     Product, Purchase, PurchaseItem, Sale, SaleItem, Supplier, utcnow)
from .money import ValidationError, line_total, money, tax_amount

D0 = Decimal("0")


class StockError(ValidationError):
    pass


def validate_password(pw):
    if len(pw or "") < 10:
        raise ValidationError("Password must be at least 10 characters.")


def audit(user_id, action, entity=None, entity_id=None, details=None):
    """Never pass passwords/tokens in details."""
    db.session.add(AuditLog(user_id=user_id, action=action, entity=entity,
                            entity_id=str(entity_id) if entity_id is not None else None,
                            details=details))


# ---------------------------------------------------------------- stock core
def _change_stock(product_id, delta, new_cost=None):
    """Conditional UPDATE: a decrease only succeeds if enough stock remains."""
    values = {"stock": Product.stock + delta}
    if new_cost is not None:
        values["cost"] = new_cost
    stmt = update(Product).where(Product.id == product_id)
    if delta < 0:
        stmt = stmt.where(Product.stock >= -delta)
    result = db.session.execute(stmt.values(**values).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise StockError("Insufficient stock for one of the items.")
    db.session.refresh(db.session.get(Product, product_id))


def _movement(product_id, change, mtype, user_id, ref_type=None, ref_id=None, reason=None):
    db.session.add(InventoryMovement(product_id=product_id, qty_change=change, movement_type=mtype,
                                     user_id=user_id, ref_type=ref_type, ref_id=ref_id, reason=reason))


def adjust_stock(user, product_id, change, mtype, reason):
    if mtype not in ("adjustment", "damaged", "expired"):
        raise ValidationError("Invalid adjustment type.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required for every adjustment.")
    if change == 0:
        raise ValidationError("Quantity change cannot be zero.")
    if mtype in ("damaged", "expired") and change > 0:
        raise ValidationError("Damaged/expired write-offs must reduce stock.")
    if not db.session.get(Product, product_id):
        raise ValidationError("Product not found.")
    try:
        _change_stock(product_id, change)
        _movement(product_id, change, mtype, user.id, "manual", None, reason)
        audit(user.id, "stock_adjust", "product", product_id, f"{mtype} {change}: {reason}")
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


# ------------------------------------------------------------------ purchases
def create_purchase(user, supplier_id, lines):
    """lines: [(product_id, qty, unit_cost)]. Creates a DRAFT; stock is untouched."""
    supplier = db.session.get(Supplier, supplier_id) if supplier_id else None
    if not supplier or not supplier.active:
        raise ValidationError("Choose a valid supplier.")
    if not lines:
        raise ValidationError("Add at least one item.")
    try:
        p = Purchase(ref=uuid.uuid4().hex, supplier_id=supplier.id, created_by=user.id)
        total = D0
        for pid, qty, cost in lines:
            prod = db.session.get(Product, pid)
            if not prod or not prod.active:
                raise ValidationError("Product not found.")
            p.items.append(PurchaseItem(product_id=pid, qty=qty, unit_cost=cost))
            total += line_total(qty, cost)
        p.total = total
        db.session.add(p)
        db.session.flush()
        p.ref = f"PO-{p.id:06d}"
        audit(user.id, "purchase_create", "purchase", p.id)
        db.session.commit()
        return p
    except Exception:
        db.session.rollback()
        raise


def receive_purchase(user, purchase_id):
    """draft -> received exactly once; stock + weighted-average cost + ledger in one transaction."""
    try:
        res = db.session.execute(
            update(Purchase).where(Purchase.id == purchase_id, Purchase.status == "draft")
            .values(status="received", received_at=utcnow())
            .execution_options(synchronize_session=False))
        if res.rowcount != 1:
            raise ValidationError("Only draft purchases can be received.")
        purchase = db.session.get(Purchase, purchase_id)
        db.session.refresh(purchase)
        for item in purchase.items:
            prod = db.session.get(Product, item.product_id)
            db.session.refresh(prod)
            new_qty = prod.stock + item.qty
            new_cost = ((prod.stock * prod.cost) + (item.qty * item.unit_cost)) / new_qty
            new_cost = new_cost.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            _change_stock(prod.id, item.qty, new_cost)
            _movement(prod.id, item.qty, "purchase", user.id, "purchase", purchase.id)
        audit(user.id, "purchase_receive", "purchase", purchase_id)
        db.session.commit()
        return purchase
    except Exception:
        db.session.rollback()
        raise


def cancel_purchase(user, purchase_id):
    res = db.session.execute(
        update(Purchase).where(Purchase.id == purchase_id, Purchase.status == "draft")
        .values(status="cancelled").execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.session.rollback()
        raise ValidationError("Only draft purchases can be cancelled.")
    audit(user.id, "purchase_cancel", "purchase", purchase_id)
    db.session.commit()


# ---------------------------------------------------------------------- sales
def create_sale(user, lines, customer_id, discount, method, paid, key, tax_rate):
    """lines: [(product_id, qty)]. Server fetches prices; client totals are never trusted.
    Returns (sale, created). Same idempotency key twice -> (existing_sale, False)."""
    if key:
        existing = Sale.query.filter_by(idempotency_key=key).first()
        if existing:
            return existing, False
    if not lines:
        raise ValidationError("Add at least one item to the sale.")
    if method not in PAYMENT_METHODS:
        raise ValidationError("Choose a valid payment method.")
    if discount < 0 or paid < 0:
        raise ValidationError("Amounts cannot be negative.")
    if discount > 0 and user.role == "cashier":
        raise ValidationError("Cashiers cannot apply discounts.")
    customer = None
    if customer_id:
        customer = db.session.get(Customer, customer_id)
        if not customer or not customer.active:
            raise ValidationError("Customer not found.")
    merged = {}
    for pid, qty in lines:
        merged[pid] = merged.get(pid, D0) + qty

    try:
        sale = Sale(invoice_no=uuid.uuid4().hex, idempotency_key=key or None,
                    customer_id=customer.id if customer else None, cashier_id=user.id,
                    payment_method=method)
        subtotal, cogs = D0, D0
        movements = []
        for pid, qty in merged.items():
            prod = db.session.get(Product, pid)
            if not prod or not prod.active:
                raise ValidationError("A product in the cart is unavailable.")
            lt = line_total(qty, prod.price)
            sale.items.append(SaleItem(product_id=prod.id, sku=prod.sku, name=prod.name, qty=qty,
                                       unit_price=prod.price, unit_cost=prod.cost, line_total=lt))
            _change_stock(prod.id, -qty)   # raises StockError if insufficient
            movements.append((prod.id, -qty))
            subtotal += lt
            cogs += money(qty * prod.cost)
        if discount > subtotal:
            raise ValidationError("Discount cannot exceed the subtotal.")
        net = subtotal - discount
        tax = tax_amount(net, tax_rate)
        total = net + tax
        if paid < total and not customer:
            raise ValidationError("Full payment is required for walk-in customers.")
        if paid > total and method != "cash":
            raise ValidationError("Overpayment is only allowed for cash.")
        recorded = min(paid, total)
        sale.subtotal, sale.discount, sale.net_sales = subtotal, discount, net
        sale.tax, sale.total, sale.paid = tax, total, recorded
        sale.change_given = paid - recorded
        sale.cogs = cogs
        if recorded > 0:
            sale.payments.append(Payment(amount=recorded, method=method, user_id=user.id))
        db.session.add(sale)
        db.session.flush()
        sale.invoice_no = f"INV-{sale.id:06d}"
        for pid, change in movements:
            _movement(pid, change, "sale", user.id, "sale", sale.id)
        audit(user.id, "sale_create", "sale", sale.id, f"total={total}")
        db.session.commit()
        return sale, True
    except IntegrityError:
        db.session.rollback()
        if key:
            existing = Sale.query.filter_by(idempotency_key=key).first()
            if existing:
                return existing, False
        raise
    except Exception:
        db.session.rollback()
        raise


def add_payment(user, sale_id, amount, method):
    if method not in PAYMENT_METHODS:
        raise ValidationError("Choose a valid payment method.")
    if amount <= 0:
        raise ValidationError("Payment must be greater than zero.")
    try:
        res = db.session.execute(
            update(Sale).where(Sale.id == sale_id, Sale.status == "completed",
                               Sale.total - Sale.paid >= amount)
            .values(paid=Sale.paid + amount).execution_options(synchronize_session=False))
        if res.rowcount != 1:
            raise ValidationError("Payment exceeds the outstanding balance.")
        db.session.add(Payment(sale_id=sale_id, amount=amount, method=method, user_id=user.id))
        audit(user.id, "payment_add", "sale", sale_id, f"{method} {amount}")
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


# ------------------------------------------------------------------ reporting
def day_bounds(offset_days=0):
    """UTC [start, end) of a business-timezone day (today - offset_days)."""
    tz = ZoneInfo(current_app.config["TIMEZONE"])
    start = (datetime.now(tz) - timedelta(days=offset_days)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    to_utc = lambda d: d.astimezone(timezone.utc).replace(tzinfo=None)
    return to_utc(start), to_utc(end)


def sales_totals(start, end):
    rows = db.session.scalars(select(Sale).where(
        Sale.status == "completed", Sale.created_at >= start, Sale.created_at < end)).all()
    net = sum((s.net_sales for s in rows), D0)
    cogs = sum((s.cogs for s in rows), D0)
    return {"net": net, "cogs": cogs, "gross": net - cogs, "count": len(rows)}


def dashboard_stats():
    t_start, t_end = day_bounds(0)
    today = sales_totals(t_start, t_end)
    month = sales_totals(day_bounds(29)[0], t_end)
    products = db.session.scalars(select(Product).where(Product.active.is_(True))).all()
    cash = db.session.scalars(select(Payment).where(Payment.created_at >= t_start, Payment.created_at < t_end)).all()
    owing = db.session.scalars(select(Sale).where(Sale.status == "completed", Sale.total > Sale.paid)).all()
    labels, values = [], []
    tz = ZoneInfo(current_app.config["TIMEZONE"])
    for i in range(6, -1, -1):
        s, e = day_bounds(i)
        labels.append(s.replace(tzinfo=timezone.utc).astimezone(tz).strftime("%d %b"))
        values.append(float(sales_totals(s, e)["net"]))
    return {
        "active_products": len(products),
        "total_stock": sum((p.stock for p in products), D0),
        "cost_value": sum((p.stock * p.cost for p in products), D0),
        "today": today, "month": month,
        "cash_today": sum((p.amount for p in cash), D0),
        "outstanding": sum((s.total - s.paid for s in owing), D0),
        "low": [p for p in products if 0 < p.stock <= p.min_stock],
        "out": [p for p in products if p.stock <= 0],
        "recent_sales": Sale.query.order_by(Sale.id.desc()).limit(8).all(),
        "recent_moves": InventoryMovement.query.order_by(InventoryMovement.id.desc()).limit(8).all(),
        "chart_labels": labels, "chart_values": values,
    }
