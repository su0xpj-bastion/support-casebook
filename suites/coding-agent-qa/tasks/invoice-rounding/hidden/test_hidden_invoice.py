from decimal import Decimal
from invoice import total

def test_half_up():
    assert total([("a", "0.005", 1)]) == Decimal("0.01")

def test_returns_decimal():
    assert isinstance(total([("a", "1.00", 2)]), Decimal)

def test_empty():
    assert total([]) == Decimal("0.00")

def test_many_lines():
    assert total([("a", "19.99", 3), ("b", "0.01", 1)]) == Decimal("59.98")
