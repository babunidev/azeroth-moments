"""Retry scheduling with exponential backoff and jitter."""

from __future__ import annotations

import random
import time


def compute_backoff_seconds(attempt: int, base: float = 5.0, cap: float = 3600.0) -> float:
    """attempt is 1-based number of failures so far."""
    exp = min(cap, base * (2 ** max(0, attempt - 1)))
    jitter = random.uniform(0, min(exp * 0.25, 30.0))
    return min(cap, exp + jitter)


def next_retry_timestamp(
    attempt: int,
    *,
    retry_after: float | None = None,
    now: float | None = None,
) -> float:
    now = now or time.time()
    if retry_after is not None and retry_after > 0:
        return now + retry_after
    return now + compute_backoff_seconds(attempt)


def is_auth_error(message: str) -> bool:
    """True for credential/permission/platform-block failures — not app rate limits."""
    lowered = message.lower()
    if (
        lowered.startswith("rate_limit:")
        or "request limit" in lowered
        or "code=4" in lowered
        or "subcode=1349210" in lowered
    ):
        return False
    if "api access blocked" in lowered or "access blocked" in lowered:
        return True
    if "code=200" in lowered and "blocked" in lowered:
        return True
    return any(
        token in lowered
        for token in (
            "oauth",
            "access token",
            "invalid token",
            "expired token",
            "permission",
            "auth:",
            "http 401",
            "status_code=401",
        )
    ) or (
        # Bare 403 only when framed as auth (permanent_auth path prefixes "auth:")
        "auth:" in lowered and "403" in lowered
    )


def is_access_blocked_error(message: str) -> bool:
    """True for Meta platform 'API access blocked' (code 200) responses."""
    lowered = message.lower()
    if lowered.startswith("rate_limit:"):
        return False
    return "api access blocked" in lowered or (
        "code=200" in lowered and "access blocked" in lowered
    )


def is_temporary_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in (
            "timeout",
            "temporarily",
            "unavailable",
            "connection",
            "429",
            "502",
            "503",
            "504",
            "network",
        )
    )
