from lru import LRU

def test_evicts_oldest():
    c = LRU(2)
    c.put("a", 1); c.put("b", 2); c.put("c", 3)
    assert c.get("a") is None and c.get("c") == 3
