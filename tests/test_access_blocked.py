from __future__ import annotations

import time
from pathlib import Path

import httpx

from azeroth_moments.db import Database
from azeroth_moments.meta_api_gate import InstagramApiGate
from azeroth_moments.meta_errors import parse_meta_error
from azeroth_moments.publishers.instagram import InstagramPublisher
from azeroth_moments.retry import is_access_blocked_error, is_auth_error
from azeroth_moments.worker import Worker
from tests.conftest import make_config


def _blocked_response() -> httpx.Response:
    return httpx.Response(
        400,
        json={
            "error": {
                "message": "API access blocked",
                "type": "OAuthException",
                "code": 200,
                "fbtrace_id": "testtrace",
            }
        },
    )


def test_parse_access_blocked():
    meta = parse_meta_error(_blocked_response())
    assert meta.access_blocked
    assert meta.permanent_auth
    assert not meta.rate_limited
    assert "API access blocked" in meta.summary()


def test_is_auth_detects_access_blocked():
    msg = "auth: create media HTTP 400 code=200 API access blocked. fbtrace_id=x"
    assert is_auth_error(msg)
    assert is_access_blocked_error(msg)
    assert not is_auth_error(
        "rate_limit: HTTP 403 code=4 subcode=1349210 Application request limit reached"
    )


def test_gate_access_block_stops_worker_instagram(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    src = tmp_path / "a.png"
    src.write_bytes(b"fake")
    sid = db.insert_screenshot(
        path=str(src),
        sha256="hash-access-1",
        author="babunigaming",
        width=100,
        height=100,
        aspect=1.0,
        shape="square",
        ingest_reason="new",
    )
    db.update_publication(sid, "instagram", status="pending")
    db.update_publication(sid, "discord", status="pending")

    gate = InstagramApiGate(db)
    gate.trip_access_blocked(detail="HTTP 400 code=200 API access blocked", now=time.time())
    assert gate.access_blocked()
    assert gate.blocked()

    worker = Worker(make_config(tmp_path, instagram_enabled=True, discord_enabled=True), db)
    worker.instagram_api_gate = gate
    worker.instagram.api_gate = gate

    ig_calls = {"n": 0}

    class FakeIG:
        def publish_prepared(self, *args, **kwargs):
            ig_calls["n"] += 1
            raise AssertionError("instagram must not publish while access blocked")

        def reconcile(self, *args, **kwargs):
            ig_calls["n"] += 1
            raise AssertionError("instagram must not reconcile while access blocked")

    discord_calls = {"n": 0}

    class FakeDiscord:
        def publish(self, path, caption):
            discord_calls["n"] += 1
            from azeroth_moments.publishers.base import PublishResult

            return PublishResult(ok=True, remote_id="d1")

    worker.instagram = FakeIG()  # type: ignore[assignment]
    worker.discord = FakeDiscord()  # type: ignore[assignment]

    assert worker._process_platform("instagram") is False
    assert ig_calls["n"] == 0
    assert worker._process_platform("discord") is True
    assert discord_calls["n"] == 1


def test_create_media_trips_access_gate(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    gate = InstagramApiGate(db)

    def handler(request: httpx.Request) -> httpx.Response:
        return _blocked_response()

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pub = InstagramPublisher(make_config(), db, client=client, api_gate=gate)
    result = pub._create_media_container(
        client,
        "123",
        "token",
        media_url="https://example.com/a.jpg",
    )
    assert not result.ok
    assert result.auth_error
    assert "API access blocked" in (result.error or "")
    assert gate.access_blocked()


def test_verify_api_access_clears_block_on_success(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    gate = InstagramApiGate(db)
    gate.trip_access_blocked(detail="prior block")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/me")
        return httpx.Response(
            200,
            json={"user_id": "999", "username": "babuni", "account_type": "MEDIA_CREATOR"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = make_config(
        instagram_enabled=True,
        instagram_account_id="999",
        instagram_access_token="tok",
    )
    pub = InstagramPublisher(cfg, db, client=client, api_gate=gate)
    result = pub.verify_api_access(force=True)
    assert result.ok
    assert not gate.access_blocked()
    assert gate.access_status() == "ok"


def test_cooldown_result_access_blocked_is_auth_not_rate_limit(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    gate = InstagramApiGate(db)
    gate.trip_access_blocked(detail="blocked")
    pub = InstagramPublisher(make_config(), db, api_gate=gate)
    result = pub.reconcile(["cid"], "")
    assert not result.ok
    assert result.auth_error
    assert "access blocked" in (result.error or "").lower()
    assert not (result.error or "").startswith("rate_limit:")
