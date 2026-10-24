"""Categories, products, suppliers, customers."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from . import services
from .auth import roles_required
from .extensions import db
from .models import Category, Customer, InventoryMovement, Product, Supplier
from .money import ValidationError, parse_decimal

bp = Blueprint("catalog", __name__)
MANAGE = ("admin", "manager")


# ------------------------------------------------------------------ categories
@bp.route("/categories", methods=["GET", "POST"])
@roles_required(*MANAGE)
def categories():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Category name is required.", "danger")
        elif Category.query.filter(func.lower(Category.name) == name.lower()).first():
            flash("Category already exists.", "danger")
        else:
            db.session.add(Category(name=name))
            services.audit(current_user.id, "category_create", "category", name)
            db.session.commit()
            flash("Category added.", "success")
        return redirect(url_for("catalog.categories"))
    return render_template("categories.html", categories=Category.query.order_by(Category.name).all())


# -------------------------------------------------------------------- products
@bp.route("/products")
@login_required
def products():
    q = request.args.get("q", "").strip()
    cat = request.args.get("category", type=int)
    stock = request.args.get("stock", "")
    stmt = select(Product).order_by(Product.name)
    if not request.args.get("inactive"):
        stmt = stmt.where(Product.active.is_(True))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.sku.ilike(like), Product.barcode.ilike(like)))
    if cat:
        stmt = stmt.where(Product.category_id == cat)
    if stock == "out":
        stmt = stmt.where(Product.stock <= 0)
    elif stock == "low":
        stmt = stmt.where(Product.stock > 0, Product.stock <= Product.min_stock)
    page = db.paginate(stmt, page=request.args.get("page", 1, type=int), per_page=15, error_out=False)
    return render_template("products.html", page=page, categories=Category.query.order_by(Category.name).all())


def _parse_product(existing):
    f = request.form
    name = f.get("name", "").strip()
    sku = f.get("sku", "").strip().upper()
    if not name:
        raise ValidationError("Product name is required.")
    if not sku:
        raise ValidationError("SKU is required.")
    dup = Product.query.filter(Product.sku == sku)
    if existing:
        dup = dup.filter(Product.id != existing.id)
    if dup.first():
        raise ValidationError("SKU already exists.")
    barcode = f.get("barcode", "").strip() or None
    if barcode:
        bd = Product.query.filter(Product.barcode == barcode)
        if existing:
            bd = bd.filter(Product.id != existing.id)
        if bd.first():
            raise ValidationError("Barcode already exists.")
    category_id = f.get("category_id", type=int)
    if category_id and not db.session.get(Category, category_id):
        raise ValidationError("Invalid category.")
    data = dict(name=name, sku=sku, barcode=barcode, category_id=category_id or None,
                description=f.get("description", "").strip(), unit=f.get("unit", "").strip() or "pcs",
                price=parse_decimal(f.get("price"), "Selling price", places=2),
                min_stock=parse_decimal(f.get("min_stock"), "Minimum stock", places=3))
    data["cost"] = parse_decimal(f.get("cost"), "Purchase cost", places=4)
    data["opening"] = None if existing else parse_decimal(f.get("stock"), "Opening stock", places=3)
    return data


@bp.route("/products/new", methods=["GET", "POST"], defaults={"pid": None})
@bp.route("/products/<int:pid>/edit", methods=["GET", "POST"])
@roles_required(*MANAGE)
def product_form(pid):
    product = db.get_or_404(Product, pid) if pid else None
    if request.method == "POST":
        try:
            d = _parse_product(product)
            opening = d.pop("opening")
            if product is None:
                product = Product(stock=opening, **d)
                db.session.add(product)
                db.session.flush()
                if opening > 0:
                    db.session.add(InventoryMovement(product_id=product.id, qty_change=opening,
                                                     movement_type="opening", user_id=current_user.id))
                services.audit(current_user.id, "product_create", "product", product.id)
            else:
                changes = [f"{k}: {getattr(product, k)} -> {v}" for k, v in d.items()
                           if k in ("cost", "price") and getattr(product, k) != v]
                for k, v in d.items():
                    setattr(product, k, v)
                services.audit(current_user.id, "product_update", "product", product.id, "; ".join(changes) or None)
            db.session.commit()
            flash("Product saved.", "success")
            return redirect(url_for("catalog.products"))
        except ValidationError as e:
            db.session.rollback()
            flash(str(e), "danger")
        except IntegrityError:
            db.session.rollback()
            flash("SKU or barcode already exists.", "danger")
    return render_template("product_form.html", product=product, form=request.form,
                           categories=Category.query.order_by(Category.name).all())


def _toggle(model, pid, label):
    obj = db.get_or_404(model, pid)
    obj.active = not obj.active
    services.audit(current_user.id, f"{label}_toggle", label, pid, f"active={obj.active}")
    db.session.commit()
    flash(f"{label.title()} {'activated' if obj.active else 'deactivated'}.", "success")


@bp.route("/products/<int:pid>/toggle", methods=["POST"])
@roles_required(*MANAGE)
def product_toggle(pid):
    _toggle(Product, pid, "product")
    return redirect(request.referrer or url_for("catalog.products"))


# ------------------------------------------------------ suppliers and customers
def _party_list(model, title, form_endpoint, toggle_endpoint):
    q = request.args.get("q", "").strip()
    stmt = select(model).order_by(model.name)
    if q:
        stmt = stmt.where(model.name.ilike(f"%{q}%"))
    page = db.paginate(stmt, page=request.args.get("page", 1, type=int), per_page=20, error_out=False)
    return render_template("parties.html", page=page, title=title, form_endpoint=form_endpoint,
                           toggle_endpoint=toggle_endpoint)


def _party_form(model, pid, title, list_endpoint, extra=()):
    obj = db.get_or_404(model, pid) if pid else None
    if request.method == "POST":
        f = request.form
        name = f.get("name", "").strip()
        email = f.get("email", "").strip()
        if not name:
            flash("Name is required.", "danger")
        elif email and "@" not in email:
            flash("Email looks invalid.", "danger")
        else:
            if obj is None:
                obj = model(name=name)
                db.session.add(obj)
            for field in ("name", "phone", "email", "address", "notes", *extra):
                setattr(obj, field, f.get(field, "").strip())
            db.session.flush()
            services.audit(current_user.id, f"{model.__name__.lower()}_save", model.__name__.lower(), obj.id)
            db.session.commit()
            flash(f"{title} saved.", "success")
            return redirect(url_for(list_endpoint))
    return render_template("party_form.html", obj=obj, form=request.form, title=title,
                           is_supplier=model is Supplier)


@bp.route("/suppliers")
@roles_required(*MANAGE)
def suppliers():
    return _party_list(Supplier, "Suppliers", "catalog.supplier_form", "catalog.supplier_toggle")


@bp.route("/suppliers/new", methods=["GET", "POST"], defaults={"pid": None})
@bp.route("/suppliers/<int:pid>/edit", methods=["GET", "POST"])
@roles_required(*MANAGE)
def supplier_form(pid):
    return _party_form(Supplier, pid, "Supplier", "catalog.suppliers", extra=("contact_person",))


@bp.route("/suppliers/<int:pid>/toggle", methods=["POST"])
@roles_required(*MANAGE)
def supplier_toggle(pid):
    _toggle(Supplier, pid, "supplier")
    return redirect(url_for("catalog.suppliers"))


@bp.route("/customers")
@roles_required(*MANAGE)
def customers():
    return _party_list(Customer, "Customers", "catalog.customer_form", "catalog.customer_toggle")


@bp.route("/customers/new", methods=["GET", "POST"], defaults={"pid": None})
@bp.route("/customers/<int:pid>/edit", methods=["GET", "POST"])
@roles_required(*MANAGE)
def customer_form(pid):
    return _party_form(Customer, pid, "Customer", "catalog.customers")


@bp.route("/customers/<int:pid>/toggle", methods=["POST"])
@roles_required(*MANAGE)
def customer_toggle(pid):
    _toggle(Customer, pid, "customer")
    return redirect(url_for("catalog.customers"))
