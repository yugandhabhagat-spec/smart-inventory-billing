"""Pure money/quantity helpers (no database imports)."""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")


class ValidationError(ValueError):
    """User-correctable input problem."""


def money(value):
    """Round to 2 places, half-up (documented rounding policy)."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def line_total(qty, unit_price):
    return money(Decimal(qty) * Decimal(unit_price))


def tax_amount(taxable, rate_percent):
    return money(Decimal(taxable) * Decimal(rate_percent) / Decimal(100))


def parse_decimal(raw, label, minimum=Decimal("0"), maximum=Decimal("999999999"),
                  places=None, positive=False):
    """Strictly parse user input. Never turns bad input into zero."""
    text = "" if raw is None else str(raw).strip()
    if not text:
        raise ValidationError(f"{label} is required.")
    try:
        d = Decimal(text)
    except InvalidOperation:
        raise ValidationError(f"{label} must be a number.")
    if not d.is_finite():
        raise ValidationError(f"{label} must be a number.")
    if positive and d <= 0:
        raise ValidationError(f"{label} must be greater than zero.")
    if d < minimum:
        raise ValidationError(f"{label} cannot be less than {minimum}.")
    if d > maximum:
        raise ValidationError(f"{label} is too large.")
    if places is not None and d != d.quantize(Decimal(1).scaleb(-places)):
        raise ValidationError(f"{label} can have at most {places} decimal places.")
    return d


def fmt_qty(q):
    return format(Decimal(q).normalize(), "f")


def fmt_money(v, currency=""):
    return f"{currency} {Decimal(v):,.2f}".strip()
