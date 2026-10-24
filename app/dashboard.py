from flask import Blueprint, redirect, render_template, url_for
from flask_login import current_user, login_required

from . import services

bp = Blueprint("dashboard", __name__)


@bp.route("/")
@login_required
def index():
    if current_user.role == "cashier":
        return redirect(url_for("sales.new_sale"))
    return render_template("dashboard.html", **services.dashboard_stats())
