from lru import LRU

def test_get_refreshes():
    c = LRU(2)
    c.put("a", 1); c.put("b", 2)
    c.get("a")
    c.put("c", 3)
    assert c.get("b") is None and c.get("a") == 1

def test_update_refreshes():
    c = LRU(2)
    c.put("a", 1); c.put("b", 2); c.put("a", 9); c.put("c", 3)
    assert c.get("a") == 9 and c.get("b") is None

def test_capacity_one():
    c = LRU(1)
    c.put("a", 1); c.put("b", 2)
    assert c.get("a") is None and c.get("b") == 2
