from app.extensions import db
from app.models import Product, User

from .conftest import PW, login, make_product, make_user


def test_anonymous_redirected_to_login(client):
    r = client.get("/products")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_login_logout(client):
    make_user("manager")
    assert login(client, "manager").status_code == 302
    assert client.get("/products").status_code == 200
    client.post("/logout")
    assert client.get("/products").status_code == 302


def test_wrong_password_rejected(client):
    make_user("manager")
    r = client.post("/login", data={"username": "manager", "password": "nope"})
    assert r.status_code == 200 and b"Invalid" in r.data


def test_lockout_after_repeated_failures(client):
    make_user("manager")
    for _ in range(5):
        client.post("/login", data={"username": "manager", "password": "bad"})
    r = login(client, "manager")          # correct password, but locked
    assert r.status_code == 200
    assert client.get("/products").status_code == 302


def test_inactive_user_cannot_login(client):
    u = make_user("manager")
    u.active = False
    db.session.commit()
    assert login(client, "manager").status_code == 200
    assert client.get("/products").status_code == 302


def test_cashier_forbidden_from_admin_and_manager_areas(client):
    make_user("cashier")
    login(client, "cashier")
    for url in ("/employees", "/audit", "/purchases", "/inventory", "/suppliers", "/customers", "/products/new"):
        assert client.get(url).status_code == 403, url
    assert client.post("/products/new", data={"name": "x", "sku": "X"}).status_code == 403


def test_manager_cannot_open_employees(client):
    make_user("manager")
    login(client, "manager")
    assert client.get("/employees").status_code == 403


def test_open_redirect_blocked(client):
    make_user("manager")
    r = client.post("/login?next=//evil.com", data={"username": "manager", "password": PW})
    assert "evil.com" not in r.headers["Location"]


def test_admin_creates_employee_and_dashboard_empty_state(client):
    make_user("admin")
    login(client, "admin")
    assert client.get("/").status_code == 200   # empty database
    r = client.post("/employees/new", data={"username": "sam", "full_name": "Sam", "role": "cashier",
                                            "password": "longenough-1"})
    assert r.status_code == 302
    assert User.query.filter_by(username="sam").first().role == "cashier"


def test_cashier_cannot_see_other_cashiers_invoice(client):
    from decimal import Decimal
    from app.services import create_sale
    a, b = make_user("cashier", "c1"), make_user("cashier", "c2")
    p = make_product()
    sale, _ = create_sale(a, [(p.id, Decimal("1"))], None, Decimal("0"), "cash", Decimal("10"), "k", Decimal("0"))
    login(client, "c2")
    assert client.get(f"/sales/{sale.id}").status_code == 403
    assert client.get(f"/sales/{sale.id}/pdf").status_code == 403


def test_duplicate_sku_rejected_and_bad_input_not_zeroed(client):
    make_user("manager")
    login(client, "manager")
    data = {"name": "A", "sku": "dup", "cost": "1", "price": "2", "min_stock": "0", "stock": "0"}
    client.post("/products/new", data=data)
    client.post("/products/new", data=data)
    assert Product.query.count() == 1
    client.post("/products/new", data={**data, "sku": "other", "price": "abc"})
    assert Product.query.count() == 1
    client.post("/products/new", data={**data, "sku": "neg", "price": "-5"})
    assert Product.query.count() == 1
