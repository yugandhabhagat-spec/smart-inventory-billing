from decimal import Decimal

import pytest

from app import create_app
from app.extensions import db
from app.models import Customer, Product, Supplier, User
from config import TestConfig

PW = "correct-horse-1"


@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def make_user(role="manager", username=None):
    u = User(username=username or role, full_name=role.title(), role=role)
    u.set_password(PW)
    db.session.add(u)
    db.session.commit()
    return u


def login(client, username):
    return client.post("/login", data={"username": username, "password": PW})


def make_product(sku="P1", price="10", cost="6", stock="10", minimum="2"):
    p = Product(sku=sku, name=f"Item {sku}", price=Decimal(price), cost=Decimal(cost),
                stock=Decimal(stock), min_stock=Decimal(minimum))
    db.session.add(p)
    db.session.commit()
    return p


def make_customer():
    c = Customer(name="Cust")
    db.session.add(c)
    db.session.commit()
    return c


def make_supplier():
    s = Supplier(name="Sup")
    db.session.add(s)
    db.session.commit()
    return s
