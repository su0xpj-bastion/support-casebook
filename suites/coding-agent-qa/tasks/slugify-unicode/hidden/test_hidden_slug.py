from slug import slugify

def test_spaces_and_symbols():
    assert slugify("  Hello,   World -- 2026 ") == "hello-world-2026"

def test_german():
    assert slugify("Über Straße") == "uber-strae"

def test_empty():
    assert slugify("!!!") == ""
