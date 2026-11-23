from bucket import TokenBucket

def test_refills_more_than_one():
    b = TokenBucket(rate=2, capacity=4)
    assert all(b.allow(0) for _ in range(4))
    assert [b.allow(2.0) for _ in range(4)] == [True, True, True, True]
