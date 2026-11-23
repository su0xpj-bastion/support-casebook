from datetime import date
from daterange import days_between

def test_inclusive():
    assert days_between(date(2026, 1, 30), date(2026, 2, 1)) == [date(2026, 1, 30), date(2026, 1, 31), date(2026, 2, 1)]
