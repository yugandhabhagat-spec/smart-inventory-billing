import os
from decimal import Decimal

import click
from flask import current_app

from . import services
from .extensions import db
from .models import Category, Customer, InventoryMovement, Product, Supplier, User
from .money import ValidationError


def register_cli(app):
    @app.cli.command("init-db")
    def init_db():
        """Create all tables (quick start). Use Flask-Migrate for versioned schema changes."""
        db.create_all()
        click.echo("Database tables created.")

    @app.cli.command("create-admin")
    @click.option("--username", prompt=True)
    @click.option("--full-name", prompt=True)
    @click.password_option()
    def create_admin(username, full_name, password):
        """Create the first administrator (refuses if one exists)."""
        if User.query.filter_by(role="admin").first():
            raise click.ClickException("An administrator already exists.")
        try:
            services.validate_password(password)
        except ValidationError as e:
            raise click.ClickException(str(e))
        u = User(username=username.strip().lower(), full_name=full_name.strip(), role="admin")
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        click.echo("Administrator created.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """DEV ONLY: fictional products/customer/supplier. Creates no user accounts."""
        if os.environ.get("APP_ENV") == "production":
            raise click.ClickException("Refusing to seed in production.")
        if Product.query.first():
            raise click.ClickException("Products already exist.")
        cats = {n: Category(name=n) for n in ("Grocery", "Beverages", "Household")}
        db.session.add_all(cats.values())
        db.session.add_all([Supplier(name="Demo Wholesale Co", phone="0000000000"),
                            Customer(name="Demo Customer", phone="1111111111")])
        db.session.flush()
        demo = [("RICE-5", "Rice 5kg", "Grocery", "kg", "40", "55", "50", "10"),
                ("OIL-1", "Sunflower Oil 1L", "Grocery", "l", "110", "140", "30", "8"),
                ("COLA-2", "Cola 2L", "Beverages", "pcs", "70", "90", "24", "6"),
                ("SOAP-1", "Soap Bar", "Household", "pcs", "20", "30", "5", "10")]
        for sku, name, cat, unit, cost, price, stock, minimum in demo:
            p = Product(sku=sku, name=name, category_id=cats[cat].id, unit=unit, cost=Decimal(cost),
                        price=Decimal(price), stock=Decimal(stock), min_stock=Decimal(minimum))
            db.session.add(p)
            db.session.flush()
            db.session.add(InventoryMovement(product_id=p.id, qty_change=Decimal(stock),
                                             movement_type="opening", reason="Demo data"))
        db.session.commit()
        click.echo("Demo data created.")
