from bucket import TokenBucket

def test_burst_then_empty():
    b = TokenBucket(rate=1, capacity=3)
    assert [b.allow(0) for _ in range(4)] == [True, True, True, False]

def test_never_above_capacity():
    b = TokenBucket(rate=10, capacity=2)
    b.allow(0); b.allow(0)
    assert [b.allow(100) for _ in range(3)] == [True, True, False]

def test_fractional_refill():
    b = TokenBucket(rate=2, capacity=1)
    assert b.allow(0)
    assert not b.allow(0.25)
    assert b.allow(0.5)
