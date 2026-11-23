from datetime import date
from daterange import days_between

def test_single_day():
    assert days_between(date(2026, 3, 1), date(2026, 3, 1)) == [date(2026, 3, 1)]

def test_reversed_is_empty():
    assert days_between(date(2026, 3, 2), date(2026, 3, 1)) == []

def test_leap_year():
    assert len(days_between(date(2028, 2, 1), date(2028, 2, 29))) == 29
