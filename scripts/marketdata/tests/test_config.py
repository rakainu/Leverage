from lighter_gateway.config import load_config

def test_load_and_ttl_prefix_match(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "upstream: https://up.example\n"
        "listen: {host: 0.0.0.0, port: 8060}\n"
        "rate_limit: {rate_per_s: 4.0, burst: 8}\n"
        "max_stale_s: 15\n"
        "cache_capacity: 1000\n"
        "ttl:\n"
        "  /api/v1/candlesticks: 20\n"
        "  /api/v1/orderBook: 2.5\n"
        "  default: 2.0\n"
    )
    c = load_config(str(p))
    assert c.upstream == "https://up.example"
    assert c.port == 8060
    assert c.rate_per_s == 4.0
    assert c.ttl_for("/api/v1/candlesticks?x=1".split("?")[0]) == 20
    assert c.ttl_for("/api/v1/orderBookOrders") == 2.5   # longest prefix
    assert c.ttl_for("/api/v1/unknown") == 2.0           # default
