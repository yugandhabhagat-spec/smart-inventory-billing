from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from flask_login import UserMixin
from sqlalchemy import BigInteger
from sqlalchemy.types import TypeDecorator
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db

ROLES = ("admin", "manager", "cashier")
PAYMENT_METHODS = ("cash", "upi", "card", "other")
D0 = Decimal("0")


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Dec(TypeDecorator):
    """Exact decimal stored as a scaled integer (no binary floats, SQL math stays exact)."""
    impl = BigInteger
    cache_ok = True

    def __init__(self, scale=2):
        super().__init__()
        self.scale = scale

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        d = value if isinstance(value, Decimal) else Decimal(str(value))
        return int((d * (10 ** self.scale)).to_integral_value(rounding=ROUND_HALF_UP))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(int(value)).scaleb(-self.scale)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(32), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="cashier")
    active = db.Column(db.Boolean, nullable=False, default=True)
    failed_logins = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    @property
    def is_active(self):
        return self.active

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)


class Supplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, index=True)
    contact_person = db.Column(db.String(120))
    phone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    address = db.Column(db.Text)
    notes = db.Column(db.Text)
    active = db.Column(db.Boolean, nullable=False, default=True)


class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, index=True)
    phone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    address = db.Column(db.Text)
    notes = db.Column(db.Text)
    active = db.Column(db.Boolean, nullable=False, default=True)


class Product(db.Model):
    __table_args__ = (
        db.CheckConstraint("stock >= 0", name="ck_product_stock_nonneg"),
        db.CheckConstraint("price >= 0", name="ck_product_price_nonneg"),
        db.CheckConstraint("cost >= 0", name="ck_product_cost_nonneg"),
    )
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(50), unique=True, nullable=False, index=True)
    barcode = db.Column(db.String(50), unique=True)
    name = db.Column(db.String(160), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"))
    description = db.Column(db.Text)
    unit = db.Column(db.String(20), nullable=False, default="pcs")
    cost = db.Column(Dec(4), nullable=False, default=D0)      # weighted-average cost
    price = db.Column(Dec(2), nullable=False, default=D0)
    stock = db.Column(Dec(3), nullable=False, default=D0)
    min_stock = db.Column(Dec(3), nullable=False, default=D0)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    category = db.relationship("Category")

    @property
    def stock_status(self):
        if self.stock <= 0:
            return "OUT OF STOCK"
        if self.stock <= self.min_stock:
            return "LOW STOCK"
        return "OK"

    @property
    def suggested_reorder(self):
        return max(self.min_stock * 2 - self.stock, D0)


class Purchase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ref = db.Column(db.String(40), unique=True, nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey("supplier.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="draft")  # draft/received/cancelled
    total = db.Column(Dec(2), nullable=False, default=D0)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    received_at = db.Column(db.DateTime)
    supplier = db.relationship("Supplier")
    items = db.relationship("PurchaseItem", backref="purchase", cascade="all, delete-orphan")


class PurchaseItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey("purchase.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    qty = db.Column(Dec(3), nullable=False)
    unit_cost = db.Column(Dec(4), nullable=False)
    product = db.relationship("Product")


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_no = db.Column(db.String(40), unique=True, nullable=False)
    idempotency_key = db.Column(db.String(64), unique=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"))
    cashier_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="completed")
    payment_method = db.Column(db.String(20), nullable=False)
    subtotal = db.Column(Dec(2), nullable=False, default=D0)
    discount = db.Column(Dec(2), nullable=False, default=D0)
    net_sales = db.Column(Dec(2), nullable=False, default=D0)   # subtotal - discount (revenue, ex-tax)
    tax = db.Column(Dec(2), nullable=False, default=D0)
    total = db.Column(Dec(2), nullable=False, default=D0)
    paid = db.Column(Dec(2), nullable=False, default=D0)
    change_given = db.Column(Dec(2), nullable=False, default=D0)
    cogs = db.Column(Dec(2), nullable=False, default=D0)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    customer = db.relationship("Customer")
    cashier = db.relationship("User")
    items = db.relationship("SaleItem", backref="sale", cascade="all, delete-orphan")
    payments = db.relationship("Payment", backref="sale", cascade="all, delete-orphan")

    @property
    def balance(self):
        return self.total - self.paid


class SaleItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    sku = db.Column(db.String(50), nullable=False)       # historical snapshot
    name = db.Column(db.String(160), nullable=False)     # historical snapshot
    qty = db.Column(Dec(3), nullable=False)
    unit_price = db.Column(Dec(2), nullable=False)       # historical snapshot
    unit_cost = db.Column(Dec(4), nullable=False)        # historical cost basis
    line_total = db.Column(Dec(2), nullable=False)


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False, index=True)
    amount = db.Column(Dec(2), nullable=False)
    method = db.Column(db.String(20), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)


class InventoryMovement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False, index=True)
    qty_change = db.Column(Dec(3), nullable=False)
    movement_type = db.Column(db.String(20), nullable=False)  # opening/purchase/sale/adjustment/damaged/expired
    ref_type = db.Column(db.String(20))
    ref_id = db.Column(db.Integer)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reason = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    product = db.relationship("Product")
    user = db.relationship("User")


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    action = db.Column(db.String(60), nullable=False)
    entity = db.Column(db.String(40))
    entity_id = db.Column(db.String(40))
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    user = db.relationship("User")
