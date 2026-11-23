import pytest
from pager import paginate

def test_pages_cover_everything_once():
    items = list(range(10))
    got = [x for p in range(1, 5) for x in paginate(items, p, 3)]
    assert got == items

def test_last_partial_page():
    assert paginate(list(range(10)), 4, 3) == [9]

def test_past_the_end_is_empty():
    assert paginate([1, 2], 3, 2) == []

def test_invalid():
    with pytest.raises(ValueError):
        paginate([1], 0, 1)
