from lighter_gateway.cache import ResponseCache, CachedResponse

def _e(ts, body=b"x"):
    return CachedResponse(status=200, body=body, content_type="application/json", fetched_monotonic=ts)

def test_get_returns_put_entry():
    c = ResponseCache(capacity=2)
    c.put("a", _e(1.0, b"A"))
    got = c.get("a")
    assert got is not None and got.body == b"A"
    assert c.get("missing") is None

def test_lru_eviction_by_capacity():
    c = ResponseCache(capacity=2)
    c.put("a", _e(1.0)); c.put("b", _e(2.0))
    c.get("a")                      # touch 'a' so 'b' is now LRU
    c.put("c", _e(3.0))             # evicts 'b'
    assert c.get("a") is not None
    assert c.get("b") is None
    assert c.get("c") is not None
