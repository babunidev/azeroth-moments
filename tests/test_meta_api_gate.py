from __future__ import annotations

import time
from pathlib import Path

import httpx

from azeroth_moments.db import Database
from azeroth_moments.meta_api_gate import InstagramApiGate
from azeroth_moments.publishers.instagram import InstagramPublisher
from azeroth_moments.worker import Worker
from tests.conftest import make_config


def test_gate_persists_across_instances(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    gate = InstagramApiGate(db)
    remaining = gate.trip_rate_limit(retry_after=60, detail="code=4", now=1_000.0)
    assert remaining >= 300.0  # floor
    gate2 = InstagramApiGate(db)
    assert gate2.blocked(now=1_000.0)
    assert gate2.remaining(now=1_000.0) >= 300.0
    assert not gate2.blocked(now=1_000.0 + remaining + 1)


def test_wait_stops_on_first_rate_limit_and_trips_gate(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            403,
            json={
                "error": {
                    "message": "Application request limit reached",
                    "type": "OAuthException",
                    "code": 4,
                    "error_subcode": 1349210,
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gate = InstagramApiGate(db)
    pub = InstagramPublisher(make_config(), db, client=client, api_gate=gate)
    result = pub._wait_container_ready(
        client, "cid1", "token", is_video=True, interval=60.0, max_duration=300
    )
    assert not result.ok
    assert result.ambiguous
    assert "rate_limit" in (result.error or "")
    assert calls["n"] == 1  # no local retry storm
    assert gate.blocked()


def test_worker_skips_instagram_when_gate_blocked_discord_ok(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    src = tmp_path / "a.png"
    src.write_bytes(b"fake")
    sid = db.insert_screenshot(
        path=str(src),
        sha256="hash-gate-1",
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
    gate.trip_rate_limit(retry_after=600, detail="test", now=time.time())

    worker = Worker(make_config(tmp_path, instagram_enabled=True, discord_enabled=True), db)
    worker.instagram_api_gate = gate
    worker.instagram.api_gate = gate

    ig_calls = {"n": 0}

    class FakeIG:
        def publish_prepared(self, *args, **kwargs):
            ig_calls["n"] += 1
            raise AssertionError("instagram must not publish during cooldown")

        def reconcile(self, *args, **kwargs):
            ig_calls["n"] += 1
            raise AssertionError("instagram must not reconcile during cooldown")

    discord_calls = {"n": 0}

    class FakeDiscord:
        def publish(self, path, caption):
            discord_calls["n"] += 1
            from azeroth_moments.publishers.base import PublishResult

            return PublishResult(ok=True, remote_id="d1")

    worker.instagram = FakeIG()  # type: ignore[assignment]
    worker.discord = FakeDiscord()  # type: ignore[assignment]

    # Instagram blocked by gate
    assert worker._process_platform("instagram") is False
    assert ig_calls["n"] == 0
    # Discord still independent
    assert worker._process_platform("discord") is True
    assert discord_calls["n"] == 1


def test_publish_respects_preexisting_gate(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    gate = InstagramApiGate(db)
    gate.trip_rate_limit(detail="pre", now=time.time())
    pub = InstagramPublisher(make_config(), db, api_gate=gate)
    result = pub.reconcile(["keep-me"], "")
    assert not result.ok
    assert result.ambiguous
    assert result.container_ids == ["keep-me"]
    assert "cooldown" in (result.error or "").lower()
