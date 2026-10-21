"""Persistent, process-wide Instagram Graph API cooldown / access block.

Meta app request limits (HTTP 403 code 4 / subcode 1349210) apply to the whole
app, not a single publication. Platform "API access blocked" (HTTP 400 code 200)
is a Meta-side restriction that must also stop all Graph traffic until cleared.

This gate is shared by publish, status polling, reconcile, and recovery so one
workflow cannot keep burning quota or hammering a blocked app. Survives service
restarts via the SQLite control table.
"""

from __future__ import annotations

import logging
import time

from azeroth_moments.db import Database

log = logging.getLogger(__name__)

KEY_UNTIL = "instagram_api_cooldown_until"
KEY_REASON = "instagram_api_cooldown_reason"
KEY_STRIKES = "instagram_api_cooldown_strikes"

KEY_ACCESS_BLOCKED = "instagram_api_access_blocked"
KEY_ACCESS_REASON = "instagram_api_access_reason"
KEY_ACCESS_STATUS = "instagram_api_access_status"
KEY_ACCESS_CHECKED_AT = "instagram_api_access_checked_at"

# Floor when Meta rate-limits us; escalate with consecutive trips.
DEFAULT_COOLDOWN_SECONDS = 300.0
MAX_COOLDOWN_SECONDS = 3600.0
STRIKE_RESET_AFTER = 7200.0  # calm window before strike counter resets

# While Meta blocks API access, defer Instagram work without re-probing.
ACCESS_BLOCK_DEFER_SECONDS = 86400.0


class InstagramApiGate:
    def __init__(self, db: Database) -> None:
        self.db = db

    def remaining(self, *, now: float | None = None) -> float:
        """Seconds until Instagram Graph calls may resume (rate limit or access block)."""
        now = now if now is not None else time.time()
        if self.access_blocked():
            return ACCESS_BLOCK_DEFER_SECONDS
        until = self._until()
        return max(0.0, until - now)

    def blocked(self, *, now: float | None = None) -> bool:
        """True when Instagram Graph calls must not be made."""
        return self.access_blocked() or self.rate_limited(now=now)

    def rate_limited(self, *, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        return max(0.0, self._until() - now) > 0

    def access_blocked(self) -> bool:
        return self.db.get_control(KEY_ACCESS_BLOCKED, "0") == "1"

    def reason(self) -> str:
        if self.access_blocked():
            return self.db.get_control(KEY_ACCESS_REASON, "") or "API access blocked"
        return self.db.get_control(KEY_REASON, "")

    def access_status(self) -> str:
        """ok | blocked | auth_error | error | unverified"""
        if self.access_blocked():
            return "blocked"
        raw = self.db.get_control(KEY_ACCESS_STATUS, "")
        return raw or "unverified"

    def access_checked_at(self) -> float:
        raw = self.db.get_control(KEY_ACCESS_CHECKED_AT, "0")
        try:
            return float(raw or 0)
        except ValueError:
            return 0.0

    def snapshot(self, *, now: float | None = None) -> dict[str, float | str | bool | int]:
        now = now if now is not None else time.time()
        rate_remaining = max(0.0, self._until() - now)
        access = self.access_blocked()
        return {
            "blocked": access or rate_remaining > 0,
            "access_blocked": access,
            "rate_limited": rate_remaining > 0,
            "remaining_seconds": round(self.remaining(now=now), 1),
            "rate_limit_remaining_seconds": round(rate_remaining, 1),
            "cooldown_until": self._until(),
            "reason": self.reason(),
            "strikes": self._strikes(),
            "api_access_status": self.access_status(),
            "api_access_reason": self.db.get_control(KEY_ACCESS_REASON, ""),
            "api_access_checked_at": self.access_checked_at(),
        }

    def trip_rate_limit(
        self,
        *,
        retry_after: float | None = None,
        detail: str = "",
        now: float | None = None,
    ) -> float:
        """Engage/extend the global cooldown. Returns seconds until clear."""
        now = now if now is not None else time.time()
        until = self._until()
        strikes = self._strikes()
        if until > now:
            # Already cooling — extend and escalate
            strikes += 1
        elif now - until > STRIKE_RESET_AFTER:
            strikes = 1
        else:
            strikes += 1

        base = max(float(retry_after or 0), DEFAULT_COOLDOWN_SECONDS)
        cooldown = min(MAX_COOLDOWN_SECONDS, base * (2 ** max(0, strikes - 1)))
        new_until = max(until, now + cooldown)
        reason = detail or "Application request limit reached"
        self.db.set_control(KEY_UNTIL, str(new_until))
        self.db.set_control(KEY_REASON, reason[:400])
        self.db.set_control(KEY_STRIKES, str(strikes))
        remaining = new_until - now
        log.warning(
            "Instagram API global cooldown engaged for %.0fs (strike=%s): %s",
            remaining,
            strikes,
            reason,
        )
        return remaining

    def trip_access_blocked(
        self,
        *,
        detail: str = "",
        now: float | None = None,
    ) -> None:
        """Stop all Instagram Graph traffic until Meta access is restored and verified."""
        now = now if now is not None else time.time()
        reason = detail or "API access blocked"
        already = self.access_blocked()
        self.db.set_control(KEY_ACCESS_BLOCKED, "1")
        self.db.set_control(KEY_ACCESS_REASON, reason[:400])
        self.db.set_control(KEY_ACCESS_STATUS, "blocked")
        self.db.set_control(KEY_ACCESS_CHECKED_AT, str(now))
        if already:
            log.warning("Instagram API access still blocked: %s", reason)
        else:
            log.error(
                "Instagram API access blocked by Meta — stopping Graph calls until "
                "verified clear. Detail: %s",
                reason,
            )

    def record_access_ok(self, *, detail: str = "", now: float | None = None) -> None:
        now = now if now is not None else time.time()
        was_blocked = self.access_blocked()
        self.db.set_control(KEY_ACCESS_BLOCKED, "0")
        self.db.set_control(KEY_ACCESS_REASON, "")
        self.db.set_control(KEY_ACCESS_STATUS, "ok")
        self.db.set_control(KEY_ACCESS_CHECKED_AT, str(now))
        if detail:
            self.db.set_control(KEY_ACCESS_REASON, detail[:400])
        if was_blocked:
            log.info("Instagram API access verified OK after prior block")

    def record_access_check(
        self,
        *,
        status: str,
        detail: str = "",
        now: float | None = None,
    ) -> None:
        """Persist a probe result without necessarily engaging the hard block."""
        now = now if now is not None else time.time()
        self.db.set_control(KEY_ACCESS_STATUS, status[:40])
        self.db.set_control(KEY_ACCESS_CHECKED_AT, str(now))
        if detail:
            self.db.set_control(KEY_ACCESS_REASON, detail[:400])
        if status == "blocked":
            self.trip_access_blocked(detail=detail, now=now)
        elif status == "ok":
            self.record_access_ok(detail=detail, now=now)

    def clear(self) -> None:
        self.db.set_control(KEY_UNTIL, "0")
        self.db.set_control(KEY_REASON, "")
        self.db.set_control(KEY_STRIKES, "0")

    def clear_access_blocked(self) -> None:
        """Clear the hard access block flag (does not prove Meta restored access)."""
        self.db.set_control(KEY_ACCESS_BLOCKED, "0")
        if self.db.get_control(KEY_ACCESS_STATUS, "") == "blocked":
            self.db.set_control(KEY_ACCESS_STATUS, "unverified")

    def _until(self) -> float:
        raw = self.db.get_control(KEY_UNTIL, "0")
        try:
            return float(raw or 0)
        except ValueError:
            return 0.0

    def _strikes(self) -> int:
        raw = self.db.get_control(KEY_STRIKES, "0")
        try:
            return max(0, int(raw or 0))
        except ValueError:
            return 0
