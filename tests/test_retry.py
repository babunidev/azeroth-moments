from azeroth_moments.retry import compute_backoff_seconds, is_auth_error, next_retry_timestamp


def test_backoff_increases():
    a1 = compute_backoff_seconds(1, base=5, cap=1000)
    a3 = compute_backoff_seconds(3, base=5, cap=1000)
    assert a3 >= a1


def test_auth_detection():
    assert is_auth_error("auth: token expired")
    assert is_auth_error("HTTP 401 unauthorized")
    assert is_auth_error("auth: HTTP 403 code=10 Application does not have permission")
    assert is_auth_error("auth: create media HTTP 400 code=200 API access blocked")
    assert not is_auth_error("timeout connecting")
    # App rate limits arrive as HTTP 403 code 4 — not credential failures
    assert not is_auth_error(
        "rate_limit: HTTP 403 code=4 subcode=1349210 Application request limit reached"
    )
    assert not is_auth_error("HTTP 403 code=4 Application request limit reached")


def test_retry_after_honored():
    ts = next_retry_timestamp(1, retry_after=12, now=1000.0)
    assert ts == 1012.0
