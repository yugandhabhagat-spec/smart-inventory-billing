import re

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import services
from .auth import roles_required
from .extensions import db
from .models import AuditLog, User
from .money import ValidationError

bp = Blueprint("employees", __name__)
CREATABLE = ("manager", "cashier")


@bp.route("/employees")
@roles_required("admin")
def list_users():
    return render_template("employees.html", users=User.query.order_by(User.username).all())


@bp.route("/employees/new", methods=["GET", "POST"])
@roles_required("admin")
def new_user():
    if request.method == "POST":
        f = request.form
        username = f.get("username", "").strip().lower()
        try:
            if not re.fullmatch(r"[a-z0-9_.-]{3,32}", username):
                raise ValidationError("Username: 3-32 chars, letters/digits/._-")
            if not f.get("full_name", "").strip():
                raise ValidationError("Full name is required.")
            if f.get("role") not in CREATABLE:
                raise ValidationError("Choose a valid role.")
            services.validate_password(f.get("password"))
            u = User(username=username, full_name=f["full_name"].strip(), role=f["role"])
            u.set_password(f["password"])
            db.session.add(u)
            db.session.flush()
            services.audit(current_user.id, "user_create", "user", u.id, f"role={u.role}")
            db.session.commit()
            flash("Employee created.", "success")
            return redirect(url_for("employees.list_users"))
        except ValidationError as e:
            flash(str(e), "danger")
        except IntegrityError:
            db.session.rollback()
            flash("Username already exists.", "danger")
    return render_template("employee_form.html", form=request.form)


@bp.route("/employees/<int:uid>/toggle", methods=["POST"])
@roles_required("admin")
def toggle(uid):
    u = db.get_or_404(User, uid)
    if u.id == current_user.id:
        flash("You cannot deactivate your own account.", "danger")
    else:
        u.active = not u.active
        services.audit(current_user.id, "user_toggle", "user", u.id, f"active={u.active}")
        db.session.commit()
        flash("Account updated.", "success")
    return redirect(url_for("employees.list_users"))


@bp.route("/employees/<int:uid>/reset", methods=["POST"])
@roles_required("admin")
def reset_password(uid):
    u = db.get_or_404(User, uid)
    try:
        services.validate_password(request.form.get("password"))
        u.set_password(request.form["password"])
        u.failed_logins, u.locked_until = 0, None
        services.audit(current_user.id, "password_reset", "user", u.id)   # password never logged
        db.session.commit()
        flash("Password reset.", "success")
    except ValidationError as e:
        flash(str(e), "danger")
    return redirect(url_for("employees.list_users"))


@bp.route("/audit")
@roles_required("admin")
def audit_log():
    page = db.paginate(select(AuditLog).order_by(AuditLog.id.desc()),
                       page=request.args.get("page", 1, type=int), per_page=30, error_out=False)
    return render_template("audit.html", page=page)
