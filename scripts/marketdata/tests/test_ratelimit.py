from lighter_gateway.ratelimit import TokenBucket

def test_burst_then_throttle_then_refill():
    t = [0.0]
    b = TokenBucket(rate_per_s=2.0, burst=3.0, clock=lambda: t[0])
    assert [b.try_acquire() for _ in range(3)] == [True, True, True]  # burst
    assert b.try_acquire() is False                                   # empty
    t[0] = 0.5                                                        # +0.5s -> +1 token
    assert b.try_acquire() is True
    assert b.try_acquire() is False
    t[0] = 10.0                                                       # long gap caps at burst
    assert [b.try_acquire() for _ in range(3)] == [True, True, True]
    assert b.try_acquire() is False
