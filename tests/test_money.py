from decimal import Decimal

import pytest

from app.money import ValidationError, fmt_qty, line_total, money, parse_decimal, tax_amount


def test_rounding_half_up():
    assert money("2.675") == Decimal("2.68")
    assert money("0.005") == Decimal("0.01")


def test_no_float_error():
    assert line_total(Decimal("3"), Decimal("0.10")) == Decimal("0.30")


def test_tax():
    assert tax_amount(Decimal("100"), Decimal("18")) == Decimal("18.00")


@pytest.mark.parametrize("bad", ["", "  ", "abc", "NaN", "Infinity", None, "-1", "1.2345"])
def test_parse_rejects_bad_input(bad):
    with pytest.raises(ValidationError):
        parse_decimal(bad, "X", places=3)


def test_parse_accepts_decimal_qty():
    assert parse_decimal("0.250", "Qty", positive=True, places=3) == Decimal("0.250")


def test_fmt_qty():
    assert fmt_qty(Decimal("100.000")) == "100"
    assert fmt_qty(Decimal("1.500")) == "1.5"
