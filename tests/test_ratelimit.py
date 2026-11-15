from agent.ratelimit import ChatLimiter


def test_off_by_default():
    lim = ChatLimiter(per_min=0, daily_cap=0, trust_xff=False)
    assert all(lim.check("a") is None for _ in range(100))


def test_per_ip_rolling_minute():
    t = [1000.0]
    lim = ChatLimiter(per_min=2, daily_cap=0, trust_xff=False, clock=lambda: t[0])
    assert lim.check("a") is None and lim.check("a") is None
    assert "too fast" in lim.check("a")
    assert lim.check("b") is None
    t[0] += 61
    assert lim.check("a") is None


def test_daily_cap():
    lim = ChatLimiter(per_min=0, daily_cap=2, trust_xff=False, clock=lambda: 86400.0 * 10)
    assert lim.check("a") is None and lim.check("b") is None
    assert "daily" in lim.check("c")
