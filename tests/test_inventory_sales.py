from decimal import Decimal as D

import pytest
from sqlalchemy import func

from app import services
from app.extensions import db
from app.models import InventoryMovement, Payment, Product, Purchase, Sale, SaleItem
from app.money import ValidationError

from .conftest import login, make_customer, make_product, make_supplier, make_user


def sell(user, lines, paid="1000", method="cash", customer=None, discount="0", key=None, tax="0"):
    return services.create_sale(user, lines, customer, D(discount), method, D(paid), key, D(tax))


def stock(pid):
    db.session.expire_all()
    return db.session.get(Product, pid).stock


def assert_ledger_reconciles():
    db.session.expire_all()
    for p in Product.query.all():
        total = sum((m.qty_change for m in InventoryMovement.query.filter_by(product_id=p.id)), D("0"))
        assert total == p.stock, f"{p.sku}: ledger {total} != stock {p.stock}"


# ---------------------------------------------------------------- purchases
def test_draft_does_not_change_stock_and_receive_once(app):
    u, s, p = make_user(), make_supplier(), make_product(stock="10", cost="5")
    po = services.create_purchase(u, s.id, [(p.id, D("10"), D("7"))])
    assert stock(p.id) == D("10")                       # draft: no stock change
    services.receive_purchase(u, po.id)
    assert stock(p.id) == D("20")
    with pytest.raises(ValidationError):
        services.receive_purchase(u, po.id)             # duplicate receipt blocked
    assert stock(p.id) == D("20")
    db.session.expire_all()
    assert db.session.get(Product, p.id).cost == D("6.0000")   # weighted average (10@5 + 10@7)
    assert InventoryMovement.query.filter_by(movement_type="purchase").count() == 1


def test_cancelled_purchase_cannot_be_received(app):
    u, s, p = make_user(), make_supplier(), make_product()
    po = services.create_purchase(u, s.id, [(p.id, D("5"), D("1"))])
    services.cancel_purchase(u, po.id)
    with pytest.raises(ValidationError):
        services.receive_purchase(u, po.id)
    assert stock(p.id) == D("10")


# -------------------------------------------------------------------- sales
def test_sale_deducts_stock_once_and_totals(app):
    u, p = make_user(), make_product(price="10", cost="6", stock="10")
    sale, created = sell(u, [(p.id, D("2"))], paid="50")
    assert created and stock(p.id) == D("8")
    assert (sale.subtotal, sale.total, sale.paid, sale.change_given) == (D("20"), D("20"), D("20"), D("30"))
    assert sale.cogs == D("12.00") and sale.net_sales == D("20.00")
    assert sale.invoice_no.startswith("INV-")
    assert_ledger_reconciles()


def test_discount_and_tax(app):
    u, p = make_user("manager"), make_product(price="50", stock="10")
    sale, _ = sell(u, [(p.id, D("2"))], discount="0", tax="18", paid="1000")
    assert (sale.tax, sale.total) == (D("18.00"), D("118.00"))
    sale2, _ = sell(u, [(p.id, D("2"))], discount="10", tax="10", paid="1000")
    assert (sale2.net_sales, sale2.tax, sale2.total) == (D("90.00"), D("9.00"), D("99.00"))


def test_cashier_cannot_discount(app):
    u, p = make_user("cashier"), make_product()
    with pytest.raises(ValidationError):
        sell(u, [(p.id, D("1"))], discount="1")


def test_decimal_quantity(app):
    u, p = make_user(), make_product(price="10", stock="5")
    sale, _ = sell(u, [(p.id, D("0.5"))])
    assert sale.total == D("5.00") and stock(p.id) == D("4.500")


def test_insufficient_stock_rolls_back_everything(app):
    u = make_user()
    a, b = make_product("A", stock="5"), make_product("B", stock="1")
    with pytest.raises(ValidationError):
        sell(u, [(a.id, D("2")), (b.id, D("3"))])       # line 1 ok, line 2 fails
    assert Sale.query.count() == 0 and SaleItem.query.count() == 0 and Payment.query.count() == 0
    assert stock(a.id) == D("5") and stock(b.id) == D("1")
    assert InventoryMovement.query.filter_by(movement_type="sale").count() == 0


def test_duplicate_checkout_prevented(app):
    u, p = make_user(), make_product(stock="10")
    s1, c1 = sell(u, [(p.id, D("1"))], key="same")
    s2, c2 = sell(u, [(p.id, D("1"))], key="same")
    assert c1 and not c2 and s1.id == s2.id
    assert Sale.query.count() == 1 and stock(p.id) == D("9")


def test_same_product_twice_in_cart_is_merged(app):
    u, p = make_user(), make_product(stock="3")
    sale, _ = sell(u, [(p.id, D("2")), (p.id, D("1"))])
    assert len(sale.items) == 1 and stock(p.id) == D("0")
    with pytest.raises(ValidationError):
        sell(u, [(p.id, D("1"))])                       # now out of stock


def test_historical_invoice_preserved_after_price_change(app):
    u, p = make_user(), make_product(price="10", cost="6")
    sale, _ = sell(u, [(p.id, D("1"))])
    p.price, p.cost, p.name = D("99"), D("50"), "Renamed"
    db.session.commit()
    db.session.expire_all()
    item = SaleItem.query.one()
    assert (item.unit_price, item.unit_cost, item.name) == (D("10.00"), D("6.0000"), "Item P1")
    assert db.session.get(Sale, sale.id).total == D("10.00")


def test_partial_payment_and_outstanding(app):
    u, p, c = make_user(), make_product(price="100"), make_customer()
    sale, _ = sell(u, [(p.id, D("1"))], paid="40", customer=c.id)
    assert sale.balance == D("60.00")
    services.add_payment(u, sale.id, D("60"), "upi")
    db.session.expire_all()
    assert db.session.get(Sale, sale.id).balance == D("0.00")
    with pytest.raises(ValidationError):
        services.add_payment(u, sale.id, D("1"), "cash")   # overpay blocked


def test_walk_in_must_pay_in_full(app):
    u, p = make_user(), make_product(price="100")
    with pytest.raises(ValidationError):
        sell(u, [(p.id, D("1"))], paid="40")
    assert Sale.query.count() == 0 and stock(p.id) == D("10")


def test_gross_profit_uses_historical_cost(app):
    from datetime import timedelta
    from app.models import utcnow
    u, p = make_user(), make_product(price="10", cost="6")
    sell(u, [(p.id, D("2"))])
    p.cost = D("9")                                       # later cost change must not rewrite profit
    db.session.commit()
    t = services.sales_totals(utcnow() - timedelta(days=1), utcnow() + timedelta(days=1))
    assert (t["net"], t["cogs"], t["gross"], t["count"]) == (D("20.00"), D("12.00"), D("8.00"), 1)


# -------------------------------------------------------------- adjustments
def test_adjustments_need_reason_and_never_go_negative(app):
    u, p = make_user(), make_product(stock="5")
    with pytest.raises(ValidationError):
        services.adjust_stock(u, p.id, D("-1"), "damaged", "")
    with pytest.raises(ValidationError):
        services.adjust_stock(u, p.id, D("-6"), "damaged", "broken")
    assert stock(p.id) == D("5")
    services.adjust_stock(u, p.id, D("-2"), "damaged", "dropped")
    assert stock(p.id) == D("3")
    assert_ledger_reconciles()


def test_low_stock_status(app):
    ok, low, out = make_product("A", stock="10", minimum="2"), make_product("B", stock="2", minimum="2"), make_product("C", stock="0")
    assert (ok.stock_status, low.stock_status, out.stock_status) == ("OK", "LOW STOCK", "OUT OF STOCK")
    stats = services.dashboard_stats()
    assert [p.sku for p in stats["low"]] == ["B"] and [p.sku for p in stats["out"]] == ["C"]


# ---------------------------------------------------------------- end-to-end
def test_pos_flow_and_pdf_via_http(app, client):
    make_user("manager")
    p = make_product(price="25", stock="4")
    login(client, "manager")
    r = client.post("/sales/new", data={"product_id": [str(p.id)], "qty": ["2"], "payment_method": "cash",
                                        "paid": "100", "key": "abc", "customer_id": ""})
    assert r.status_code == 302
    sale = Sale.query.one()
    assert sale.total == D("50.00")
    pdf = client.get(f"/sales/{sale.id}/pdf")
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")
    assert client.get(f"/sales/{sale.id}").status_code == 200
    bad = client.post("/sales/new", data={"product_id": [str(p.id)], "qty": ["abc"], "payment_method": "cash", "paid": "1"})
    assert bad.status_code == 200 and Sale.query.count() == 1


def test_product_search_api_hides_cost(app, client):
    make_user("cashier")
    make_product(sku="ZZ1")
    login(client, "cashier")
    data = client.get("/api/products/search?q=zz").get_json()
    assert data and "cost" not in data[0]
