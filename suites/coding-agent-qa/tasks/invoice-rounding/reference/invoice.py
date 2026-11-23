from decimal import ROUND_HALF_UP, Decimal


def total(lines):
    """lines: (name, unit_price_str, qty)"""
    amount = sum((Decimal(price) * qty for _, price, qty in lines), Decimal("0"))
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
