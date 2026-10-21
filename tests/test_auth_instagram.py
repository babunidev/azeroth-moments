from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from azeroth_moments.auth_instagram import (
    KEY_LAST_REFRESH_ATTEMPT,
    REFRESH_BEFORE_SECONDS,
    InstagramAuth,
)
from azeroth_moments.db import Database
from azeroth_moments.meta_api_gate import InstagramApiGate
from azeroth_moments.worker import KEY_TOKEN_MAINTENANCE_AT, Worker
from tests.conftest import make_config


def _auth(tmp_path: Path, monkeypatch, **cfg_overrides) -> tuple[InstagramAuth, Database]:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    cfg = make_config(
        tmp_path,
        instagram_enabled=True,
        instagram_access_token="old-token",
        **cfg_overrides,
    )
    return InstagramAuth(cfg, db), db


def test_needs_refresh_when_expiry_within_window(tmp_path: Path, monkeypatch):
    soon = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    auth, _db = _auth(tmp_path, monkeypatch, instagram_token_expires_at=soon)
    assert auth.needs_refresh()


def test_no_refresh_when_expiry_far(tmp_path: Path, monkeypatch):
    later = (
        datetime.now(timezone.utc) + timedelta(seconds=REFRESH_BEFORE_SECONDS + 86400)
    ).isoformat()
    auth, _db = _auth(tmp_path, monkeypatch, instagram_token_expires_at=later)
    assert not auth.needs_refresh()


def test_needs_refresh_when_expires_unknown(tmp_path: Path, monkeypatch):
    auth, _db = _auth(tmp_path, monkeypatch, instagram_token_expires_at="")
    assert auth.needs_refresh()


def test_refresh_persists_token_and_expiry(tmp_path: Path, monkeypatch):
    auth, db = _auth(tmp_path, monkeypatch, instagram_token_expires_at="")
    expires_in = 60 * 24 * 3600

    def handler(request: httpx.Request) -> httpx.Response:
        assert "refresh_access_token" in str(request.url)
        assert request.url.params.get("grant_type") == "ig_refresh_token"
        return httpx.Response(
            200,
            json={"access_token": "new-token", "expires_in": expires_in},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    state = auth.refresh_long_lived_token(client)
    assert state.access_token == "new-token"
    assert state.expires_at
    assert auth.token_path.is_file()
    assert auth.token_path.read_text(encoding="utf-8").strip() == "new-token"
    meta = db.get_token_meta("instagram_login")
    assert meta is not None
    assert meta["expires_at"] == state.expires_at
    assert not auth.needs_refresh()


def test_maybe_refresh_backoff_after_failure(tmp_path: Path, monkeypatch):
    auth, db = _auth(tmp_path, monkeypatch, instagram_token_expires_at="")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "nope", "code": 190}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    first = auth.maybe_refresh(client=client, force=False, now=1_000.0)
    assert first["attempted"] is True
    assert first["refreshed"] is False

    second = auth.maybe_refresh(client=client, force=False, now=1_000.0 + 60)
    assert second["attempted"] is False
    assert second["reason"] == "backoff"
    assert float(db.get_control(KEY_LAST_REFRESH_ATTEMPT, "0")) == 1_000.0


def test_worker_maintenance_refreshes_due_token(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    cfg = make_config(
        tmp_path,
        instagram_enabled=True,
        discord_enabled=False,
        instagram_access_token="old-token",
        instagram_token_expires_at="",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"access_token": "renewed", "expires_in": 60 * 24 * 3600},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    worker = Worker(cfg, db)
    worker.instagram_auth._client = None  # type: ignore[attr-defined]

    calls = {"n": 0}
    original = worker.instagram_auth.refresh_long_lived_token

    def wrapped(client_arg=None):
        calls["n"] += 1
        return original(client)

    worker.instagram_auth.refresh_long_lived_token = wrapped  # type: ignore[method-assign]
    assert worker._maintain_instagram_token() is True
    assert calls["n"] == 1
    # Interval gate: immediate second pass should no-op
    assert worker._maintain_instagram_token() is False
    assert calls["n"] == 1


def test_worker_skips_token_maintenance_when_access_blocked(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    gate = InstagramApiGate(db)
    gate.trip_access_blocked(detail="blocked", now=time.time())
    worker = Worker(
        make_config(tmp_path, instagram_enabled=True, instagram_access_token="t"),
        db,
    )
    worker.instagram_api_gate = gate

    def boom(*_a, **_k):
        raise AssertionError("must not refresh while access blocked")

    worker.instagram_auth.maybe_refresh = boom  # type: ignore[method-assign]
    assert worker._maintain_instagram_token() is False
    assert db.get_control(KEY_TOKEN_MAINTENANCE_AT, "") == ""
