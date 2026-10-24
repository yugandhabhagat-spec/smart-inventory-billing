import uuid

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_, select
from io import BytesIO

from . import services
from .extensions import db
from .models import Customer, Product, Sale
from .money import ValidationError, parse_decimal
from .pdf import build_invoice_pdf

bp = Blueprint("sales", __name__)


def _parse_cart():
    ids, qtys = request.form.getlist("product_id"), request.form.getlist("qty")
    if len(ids) != len(qtys):
        raise ValidationError("Malformed cart.")
    lines = []
    for raw_id, raw_q in zip(ids, qtys):
        try:
            pid = int(raw_id)
        except ValueError:
            raise ValidationError("Malformed cart.")
        lines.append((pid, parse_decimal(raw_q, "Quantity", positive=True, places=3)))
    return lines


def _prefill():
    out = []
    for raw_id, raw_q in zip(request.form.getlist("product_id"), request.form.getlist("qty")):
        p = db.session.get(Product, int(raw_id)) if raw_id.isdigit() else None
        if p:
            out.append({"id": p.id, "sku": p.sku, "name": p.name, "price": str(p.price), "qty": raw_q})
    return out


@bp.route("/sales/new", methods=["GET", "POST"])
@login_required
def new_sale():
    key = request.form.get("key") or uuid.uuid4().hex
    prefill = []
    if request.method == "POST":
        try:
            f = request.form
            lines = _parse_cart()
            discount = parse_decimal(f.get("discount") or "0", "Discount", places=2)
            paid = parse_decimal(f.get("paid"), "Amount received", places=2)
            sale, created = services.create_sale(current_user._get_current_object(), lines,
                                                 f.get("customer_id", type=int), discount,
                                                 f.get("payment_method", ""), paid, key,
                                                 current_app.config["TAX_RATE"])
            flash("Sale completed." if created else "This sale was already recorded.", "success")
            return redirect(url_for("sales.detail", sale_id=sale.id))
        except ValidationError as e:
            flash(str(e), "danger")
            prefill = _prefill()
    return render_template("pos.html", key=key, prefill=prefill,
                           customers=Customer.query.filter_by(active=True).order_by(Customer.name).all())


@bp.route("/api/products/search")
@login_required
def search_products():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    like = f"%{q}%"
    rows = db.session.scalars(select(Product).where(
        Product.active.is_(True), Product.stock > 0,
        or_(Product.name.ilike(like), Product.sku.ilike(like), Product.barcode == q)).limit(10)).all()
    return jsonify([{"id": p.id, "sku": p.sku, "name": p.name, "price": str(p.price),
                     "stock": str(p.stock)} for p in rows])   # cost is never exposed


def _authorized_sale(sale_id):
    sale = db.get_or_404(Sale, sale_id)
    if current_user.role == "cashier" and sale.cashier_id != current_user.id:
        abort(403)
    return sale


@bp.route("/sales")
@login_required
def sales_list():
    stmt = select(Sale).order_by(Sale.id.desc())
    if current_user.role == "cashier":
        stmt = stmt.where(Sale.cashier_id == current_user.id)
    page = db.paginate(stmt, page=request.args.get("page", 1, type=int), per_page=20, error_out=False)
    return render_template("sales.html", page=page)


@bp.route("/sales/<int:sale_id>")
@login_required
def detail(sale_id):
    return render_template("sale_detail.html", sale=_authorized_sale(sale_id))


@bp.route("/sales/<int:sale_id>/pay", methods=["POST"])
@login_required
def pay(sale_id):
    _authorized_sale(sale_id)
    try:
        amount = parse_decimal(request.form.get("amount"), "Amount", positive=True, places=2)
        services.add_payment(current_user._get_current_object(), sale_id, amount, request.form.get("method", ""))
        flash("Payment recorded.", "success")
    except ValidationError as e:
        flash(str(e), "danger")
    return redirect(url_for("sales.detail", sale_id=sale_id))


@bp.route("/sales/<int:sale_id>/pdf")
@login_required
def pdf(sale_id):
    sale = _authorized_sale(sale_id)
    c = current_app.config
    business = {"name": c["BUSINESS_NAME"], "address": c["BUSINESS_ADDRESS"], "phone": c["BUSINESS_PHONE"],
                "return_policy": c["RETURN_POLICY"], "currency": c["CURRENCY"]}
    data = build_invoice_pdf(sale, business, services_local(sale.created_at))
    return send_file(BytesIO(data), mimetype="application/pdf", download_name=f"{sale.invoice_no}.pdf")


def services_local(dt):
    from . import _local
    return _local(dt)
