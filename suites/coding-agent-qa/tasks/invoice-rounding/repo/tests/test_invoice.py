from decimal import Decimal
from invoice import total

def test_exact_cents():
    assert total([("a", "0.10", 3)]) == Decimal("0.30")
