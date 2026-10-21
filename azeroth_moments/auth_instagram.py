"""Instagram API with Instagram Login — token storage and refresh."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from azeroth_moments.config import Config
from azeroth_moments.db import Database
from azeroth_moments.paths import config_dir, default_env_candidates

log = logging.getLogger(__name__)

TOKEN_FILE = "instagram_token.txt"
GRAPH_HOST = "https://graph.instagram.com"
PROVIDER = "instagram_login"

# Long-lived Instagram Login tokens last ~60 days. Refresh well before expiry.
REFRESH_BEFORE_SECONDS = 7 * 24 * 3600
# When expires_at is unknown, refresh periodically so idle periods still renew.
UNKNOWN_EXPIRY_REFRESH_AFTER = 30 * 24 * 3600
# Do not hammer Meta after a failed refresh.
REFRESH_ATTEMPT_BACKOFF = 6 * 3600

KEY_LAST_REFRESH_ATTEMPT = "instagram_token_refresh_attempt_at"
KEY_LAST_REFRESH_ERROR = "instagram_token_refresh_last_error"


@dataclass
class TokenState:
    access_token: str
    expires_at: str  # ISO8601 or empty


class InstagramAuth:
    """Manage Instagram user access tokens for Instagram Login."""

    def __init__(self, config: Config, db: Database) -> None:
        self.config = config
        self.db = db
        self.token_path = config_dir() / TOKEN_FILE

    def current_token(self) -> TokenState:
        token = self.config.instagram_access_token
        if self.token_path.is_file():
            disk = self.token_path.read_text(encoding="utf-8").strip()
            if disk:
                token = disk
        expires = self.config.instagram_token_expires_at
        meta = self.db.get_token_meta(PROVIDER)
        if meta and meta.get("expires_at"):
            expires = meta["expires_at"]
        return TokenState(access_token=token, expires_at=expires)

    def persist_token(self, token: str, expires_at: str) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(token + "\n", encoding="utf-8")
        try:
            os.chmod(self.token_path, 0o600)
        except OSError:
            pass
        self.db.set_token_meta(PROVIDER, expires_at)
        # Also update process env for subsequent loads in this process
        os.environ["INSTAGRAM_ACCESS_TOKEN"] = token
        if expires_at:
            os.environ["INSTAGRAM_TOKEN_EXPIRES_AT"] = expires_at
            self._sync_expires_at_env(expires_at)
        log.info("Persisted Instagram token metadata expires_at=%s", expires_at or "unknown")

    def seconds_until_expiry(self, state: TokenState | None = None) -> float | None:
        state = state or self.current_token()
        if not state.expires_at:
            return None
        try:
            expires = datetime.fromisoformat(state.expires_at.replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            return (expires - datetime.now(timezone.utc)).total_seconds()
        except ValueError:
            return None

    def needs_refresh(self, state: TokenState | None = None) -> bool:
        """True when a long-lived token should be refreshed soon."""
        state = state or self.current_token()
        if not state.access_token:
            return False
        remaining = self.seconds_until_expiry(state)
        if remaining is not None:
            return remaining < REFRESH_BEFORE_SECONDS
        # Unknown expiry: refresh if never recorded, or last refresh was long ago.
        meta = self.db.get_token_meta(PROVIDER) or {}
        refreshed_at = float(meta.get("refreshed_at") or 0)
        if refreshed_at <= 0:
            return True
        return (time.time() - refreshed_at) >= UNKNOWN_EXPIRY_REFRESH_AFTER

    def refresh_long_lived_token(self, client: httpx.Client | None = None) -> TokenState:
        """Refresh a long-lived Instagram user token via graph.instagram.com."""
        state = self.current_token()
        if not state.access_token:
            raise RuntimeError("No Instagram access token available to refresh")
        owns = client is None
        client = client or httpx.Client(timeout=30.0)
        try:
            response = client.get(
                f"{GRAPH_HOST}/refresh_access_token",
                params={
                    "grant_type": "ig_refresh_token",
                    "access_token": state.access_token,
                },
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"auth: token refresh failed HTTP {response.status_code}: "
                    f"{response.text[:200]}"
                )
            data = response.json()
            new_token = data.get("access_token") or state.access_token
            expires_in = int(data.get("expires_in", 0))
            if expires_in > 0:
                expires_at = datetime.now(timezone.utc).timestamp() + expires_in
                expires_iso = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()
            else:
                expires_iso = state.expires_at
            self.persist_token(new_token, expires_iso)
            self.db.set_control(KEY_LAST_REFRESH_ERROR, "")
            return TokenState(access_token=new_token, expires_at=expires_iso)
        finally:
            if owns:
                client.close()

    def ensure_fresh_token(self, client: httpx.Client | None = None) -> TokenState:
        """Refresh if due, then return the current token (used by publishers)."""
        state = self.current_token()
        if not state.access_token:
            raise RuntimeError("auth: INSTAGRAM_ACCESS_TOKEN missing")
        if self.needs_refresh(state):
            self.maybe_refresh(client=client, force=False)
            return self.current_token()
        return state

    def maybe_refresh(
        self,
        *,
        client: httpx.Client | None = None,
        force: bool = False,
        now: float | None = None,
    ) -> dict[str, object]:
        """Attempt a refresh when due. Safe for the worker maintenance loop.

        Returns a small status dict (never includes the token value).
        """
        now = now if now is not None else time.time()
        state = self.current_token()
        if not state.access_token:
            return {"attempted": False, "refreshed": False, "reason": "no_token"}
        if not force and not self.needs_refresh(state):
            return {
                "attempted": False,
                "refreshed": False,
                "reason": "not_due",
                "expires_at": state.expires_at,
            }
        last_attempt = self._last_refresh_attempt()
        if not force and last_attempt > 0 and (now - last_attempt) < REFRESH_ATTEMPT_BACKOFF:
            return {
                "attempted": False,
                "refreshed": False,
                "reason": "backoff",
                "retry_in_seconds": round(REFRESH_ATTEMPT_BACKOFF - (now - last_attempt), 1),
            }

        self.db.set_control(KEY_LAST_REFRESH_ATTEMPT, str(now))
        try:
            new_state = self.refresh_long_lived_token(client)
            log.info(
                "Instagram long-lived token refreshed; expires_at=%s",
                new_state.expires_at or "unknown",
            )
            return {
                "attempted": True,
                "refreshed": True,
                "reason": "ok",
                "expires_at": new_state.expires_at,
            }
        except Exception as exc:  # noqa: BLE001
            detail = str(exc)[:400]
            self.db.set_control(KEY_LAST_REFRESH_ERROR, detail)
            log.error("Instagram token refresh failed: %s", detail)
            return {
                "attempted": True,
                "refreshed": False,
                "reason": "error",
                "error": detail,
                "expires_at": state.expires_at,
            }

    def snapshot(self, *, now: float | None = None) -> dict[str, object]:
        """Non-secret token health for status (no API call)."""
        now = now if now is not None else time.time()
        state = self.current_token()
        remaining = self.seconds_until_expiry(state)
        meta = self.db.get_token_meta(PROVIDER) or {}
        return {
            "present": bool(state.access_token),
            "token_file": self.token_path.is_file(),
            "expires_at": state.expires_at or "",
            "seconds_until_expiry": None if remaining is None else round(remaining, 1),
            "days_until_expiry": None if remaining is None else round(remaining / 86400, 2),
            "needs_refresh": self.needs_refresh(state),
            "refreshed_at": float(meta.get("refreshed_at") or 0),
            "last_refresh_attempt_at": self._last_refresh_attempt(),
            "last_refresh_error": self.db.get_control(KEY_LAST_REFRESH_ERROR, ""),
            "refresh_before_days": REFRESH_BEFORE_SECONDS / 86400,
            "note": (
                "Long-lived Instagram Login tokens last ~60 days; "
                "the worker refreshes them automatically before expiry"
            ),
        }

    def _last_refresh_attempt(self) -> float:
        raw = self.db.get_control(KEY_LAST_REFRESH_ATTEMPT, "0")
        try:
            return float(raw or 0)
        except ValueError:
            return 0.0

    def _sync_expires_at_env(self, expires_at: str) -> None:
        """Best-effort: keep INSTAGRAM_TOKEN_EXPIRES_AT in the active .env in sync."""
        candidates = []
        explicit = os.environ.get("AZEROTH_MOMENTS_ENV")
        if explicit:
            candidates.append(Path(explicit))
        candidates.extend(default_env_candidates())
        for path in candidates:
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            lines = text.splitlines()
            key = "INSTAGRAM_TOKEN_EXPIRES_AT="
            updated = False
            out: list[str] = []
            for line in lines:
                if line.startswith(key) or line.startswith("INSTAGRAM_TOKEN_EXPIRES_AT ="):
                    out.append(f'INSTAGRAM_TOKEN_EXPIRES_AT="{expires_at}"')
                    updated = True
                else:
                    out.append(line)
            if not updated:
                if out and out[-1].strip():
                    out.append("")
                out.append(f'INSTAGRAM_TOKEN_EXPIRES_AT="{expires_at}"')
            try:
                path.write_text("\n".join(out) + "\n", encoding="utf-8")
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
                log.info("Updated %s INSTAGRAM_TOKEN_EXPIRES_AT", path)
                return
            except OSError as exc:
                log.warning("Could not update %s expires_at: %s", path, exc)


def ensure_env_permissions_note() -> str:
    return (
        "Instagram API with Instagram Login (graph.instagram.com) requires an "
        "Instagram professional account (Business or Creator), a Meta Business-type "
        "app, and permissions such as instagram_business_basic and "
        "instagram_business_content_publish. HTTP 400 code=200 "
        "'API access blocked' is a Meta developer/app/account restriction — not a "
        "missing .env value. Long-lived tokens last ~60 days and are refreshed "
        "automatically by the worker. Store tokens in "
        f"{config_dir()} with mode 600."
    )
