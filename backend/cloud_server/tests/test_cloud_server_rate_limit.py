"""B05's token buckets, on a manual clock."""

from __future__ import annotations

from backend.cloud_server.rate_limit import RateLimiter, TokenBucket


# Input: start (float)
# Output: tuple[list, callable]
# Processing: A mutable clock a test can advance.
# Changed by: Nirmal
# Date: 2026-10-31
def fake_clock(start=0.0):
    now = [start]

    def clock():
        return now[0]

    return now, clock


# Input: none
# Output: None
# Processing: Check burst then refill.
# Changed by: Nirmal
# Date: 2026-10-31
def test_bucket_spends_then_refills():
    now, clock = fake_clock()
    bucket = TokenBucket(capacity=2, refill_per_second=1, clock=clock)
    assert bucket.take() and bucket.take()
    assert not bucket.take()
    now[0] += 1.0
    assert bucket.take()
    assert not bucket.take()


# Input: none
# Output: None
# Processing: Check that callers get independent buckets.
# Changed by: Nirmal
# Date: 2026-10-31
def test_callers_are_independent():
    _, clock = fake_clock()
    limiter = RateLimiter(capacity=1, refill_per_second=0.001, clock=clock)
    assert limiter.allow("usr_a")
    assert not limiter.allow("usr_a")
    assert limiter.allow("usr_b")


# Input: none
# Output: None
# Processing: Check that bad limits are refused.
# Changed by: Nirmal
# Date: 2026-10-31
def test_bad_limits_are_refused():
    import pytest

    with pytest.raises(ValueError):
        TokenBucket(capacity=0)
    with pytest.raises(ValueError):
        TokenBucket(refill_per_second=0)
