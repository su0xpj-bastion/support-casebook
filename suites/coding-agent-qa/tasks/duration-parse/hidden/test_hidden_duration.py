import pytest
from duration import parse_duration

@pytest.mark.parametrize("text,want", [("45s", 45), ("2m", 120), ("1h", 3600), ("1h2m3s", 3723)])
def test_units(text, want):
    assert parse_duration(text) == want

@pytest.mark.parametrize("text", ["", "10", "5x", "1m1h", "abc"])
def test_rejects(text):
    with pytest.raises(ValueError):
        parse_duration(text)
