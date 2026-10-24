from datetime import timedelta
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request,
                   session, url_for)
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import generate_password_hash

from . import services
from .extensions import db
from .models import User, utcnow

bp = Blueprint("auth", __name__)


def roles_required(*roles):
    """Server-side authorization. Hiding a button is NOT authorization."""
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*a, **kw):
            if current_user.role not in roles:
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


def _safe_next():
    nxt = request.args.get("next") or request.form.get("next") or ""
    if nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt:
        return nxt
    return url_for("dashboard.index")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        now = utcnow()
        if user and user.locked_until and user.locked_until > now:
            flash("Too many failed attempts. Try again later.", "danger")
        elif user and user.active and user.check_password(password):
            user.failed_logins, user.locked_until = 0, None
            services.audit(user.id, "login")
            db.session.commit()
            login_user(user)
            session.permanent = True
            return redirect(_safe_next())
        else:
            if user:
                user.failed_logins += 1
                if user.failed_logins >= current_app.config["MAX_LOGIN_ATTEMPTS"]:
                    user.locked_until = now + timedelta(minutes=current_app.config["LOCKOUT_MINUTES"])
                    user.failed_logins = 0
                db.session.commit()
            else:
                generate_password_hash(password)  # equalise timing
            flash("Invalid username or password.", "danger")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
