from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from . import services
from .auth import roles_required
from .extensions import db
from .models import Product, Purchase, Supplier
from .money import ValidationError, parse_decimal

bp = Blueprint("purchasing", __name__)
MANAGE = ("admin", "manager")


@bp.route("/purchases")
@roles_required(*MANAGE)
def purchases():
    page = db.paginate(select(Purchase).order_by(Purchase.id.desc()),
                       page=request.args.get("page", 1, type=int), per_page=20, error_out=False)
    return render_template("purchases.html", page=page)


@bp.route("/purchases/new", methods=["GET", "POST"])
@roles_required(*MANAGE)
def new_purchase():
    if request.method == "POST":
        try:
            f = request.form
            lines = []
            for pid, qty, cost in zip(f.getlist("product_id"), f.getlist("qty"), f.getlist("unit_cost")):
                if not pid:
                    continue
                lines.append((int(pid),
                              parse_decimal(qty, "Quantity", positive=True, places=3),
                              parse_decimal(cost, "Unit cost", places=4)))
            p = services.create_purchase(current_user, f.get("supplier_id", type=int), lines)
            flash("Purchase draft created. Stock changes only when you receive it.", "success")
            return redirect(url_for("purchasing.purchase_detail", pid=p.id))
        except (ValidationError, ValueError) as e:
            flash(str(e) if isinstance(e, ValidationError) else "Malformed item row.", "danger")
    preselect = request.args.getlist("product", type=int)
    return render_template("purchase_form.html",
                           suppliers=Supplier.query.filter_by(active=True).order_by(Supplier.name).all(),
                           products=Product.query.filter_by(active=True).order_by(Product.name).all(),
                           preselect=preselect)


@bp.route("/purchases/<int:pid>")
@roles_required(*MANAGE)
def purchase_detail(pid):
    return render_template("purchase_detail.html", p=db.get_or_404(Purchase, pid))


@bp.route("/purchases/<int:pid>/receive", methods=["POST"])
@roles_required(*MANAGE)
def receive(pid):
    try:
        services.receive_purchase(current_user, pid)
        flash("Goods received; stock updated.", "success")
    except ValidationError as e:
        flash(str(e), "danger")
    return redirect(url_for("purchasing.purchase_detail", pid=pid))


@bp.route("/purchases/<int:pid>/cancel", methods=["POST"])
@roles_required(*MANAGE)
def cancel(pid):
    try:
        services.cancel_purchase(current_user, pid)
        flash("Purchase cancelled.", "success")
    except ValidationError as e:
        flash(str(e), "danger")
    return redirect(url_for("purchasing.purchase_detail", pid=pid))
