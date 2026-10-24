from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from . import services
from .auth import roles_required
from .extensions import db
from .models import InventoryMovement, Product
from .money import ValidationError, parse_decimal

bp = Blueprint("inventory", __name__)
MANAGE = ("admin", "manager")


@bp.route("/inventory")
@roles_required(*MANAGE)
def movements():
    stmt = select(InventoryMovement).order_by(InventoryMovement.id.desc())
    pid = request.args.get("product", type=int)
    if pid:
        stmt = stmt.where(InventoryMovement.product_id == pid)
    page = db.paginate(stmt, page=request.args.get("page", 1, type=int), per_page=25, error_out=False)
    return render_template("inventory.html", page=page,
                           products=Product.query.filter_by(active=True).order_by(Product.name).all())


@bp.route("/inventory/adjust", methods=["POST"])
@roles_required(*MANAGE)
def adjust():
    f = request.form
    try:
        change = parse_decimal(f.get("change"), "Quantity change", minimum=-999999999, places=3)
        services.adjust_stock(current_user, f.get("product_id", type=int), change,
                              f.get("movement_type", ""), f.get("reason"))
        flash("Stock adjusted and logged.", "success")
    except ValidationError as e:
        flash(str(e), "danger")
    return redirect(url_for("inventory.movements"))
