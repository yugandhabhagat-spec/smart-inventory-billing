import os
from datetime import timezone
from zoneinfo import ZoneInfo

from flask import Flask, current_app, render_template
from flask_wtf.csrf import CSRFError
from sqlalchemy import event
from sqlalchemy.engine import Engine

from config import Config

from .extensions import csrf, db, login_manager, migrate
from .money import fmt_money, fmt_qty


@event.listens_for(Engine, "connect")
def _sqlite_foreign_keys(dbapi_conn, record):
    if dbapi_conn.__class__.__module__.startswith("sqlite3"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def _local(dt, fmt="%d %b %Y %H:%M"):
    if not dt:
        return ""
    tz = ZoneInfo(current_app.config["TIMEZONE"])
    return dt.replace(tzinfo=timezone.utc).astimezone(tz).strftime(fmt)


def create_app(config_object=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object or Config)
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY is not set. Copy .env.example to .env and set it.")
    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    csrf.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    from .models import User

    @login_manager.user_loader
    def load_user(uid):
        u = db.session.get(User, int(uid))
        return u if u and u.active else None   # deactivated users are logged out at once

    from . import auth, catalog, dashboard, employees, inventory, purchasing, sales
    for m in (auth, dashboard, catalog, inventory, purchasing, sales, employees):
        app.register_blueprint(m.bp)

    app.jinja_env.filters["money"] = lambda v: fmt_money(v, app.config["CURRENCY"])
    app.jinja_env.filters["qty"] = fmt_qty
    app.jinja_env.filters["local"] = _local

    @app.context_processor
    def inject():
        return {"business_name": app.config["BUSINESS_NAME"], "tax_rate": app.config["TAX_RATE"]}

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return resp

    @app.errorhandler(403)
    def e403(e):
        return render_template("error.html", code=403, message="Access denied. You do not have permission."), 403

    @app.errorhandler(404)
    def e404(e):
        return render_template("error.html", code=404, message="Page not found."), 404

    @app.errorhandler(CSRFError)
    def ecsrf(e):
        return render_template("error.html", code=400, message="Session expired or invalid form. Reload and retry."), 400

    @app.errorhandler(500)
    def e500(e):
        db.session.rollback()
        return render_template("error.html", code=500, message="Something went wrong."), 500

    from .cli import register_cli
    register_cli(app)
    return app
