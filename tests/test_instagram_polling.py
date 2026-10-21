from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from azeroth_moments.db import Database
from azeroth_moments.meta_errors import parse_meta_error
from azeroth_moments.publishers.instagram import InstagramPublisher
from azeroth_moments.worker import Worker
from tests.conftest import make_config


def _response(status: int, payload: dict, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        headers=headers or {},
        request=httpx.Request("GET", "https://graph.instagram.com/v22.0/1"),
    )


def test_parse_rate_limit_403():
    resp = _response(
        403,
        {
            "error": {
                "message": "Application request limit reached",
                "type": "OAuthException",
                "code": 4,
                "error_subcode": 1349210,
                "fbtrace_id": "abc",
            }
        },
    )
    meta = parse_meta_error(resp)
    assert meta.rate_limited
    assert not meta.permanent_auth
    assert meta.code == 4
    assert meta.error_subcode == 1349210
    assert "request limit" in meta.message.lower()
    assert "access_token" not in meta.summary().lower() or "***" in meta.summary()


def test_parse_permission_403_is_permanent_auth():
    resp = _response(
        403,
        {
            "error": {
                "message": "Application does not have permission for this action",
                "type": "OAuthException",
                "code": 10,
            }
        },
    )
    meta = parse_meta_error(resp)
    assert meta.permanent_auth
    assert not meta.rate_limited


def test_wait_stops_on_permanent_403(tmp_path: Path, monkeypatch):
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
                    "message": "Application does not have permission for this action",
                    "type": "OAuthException",
                    "code": 10,
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pub = InstagramPublisher(make_config(), db, client=client)
    result = pub._wait_container_ready(client, "cid1", "token", is_video=False, max_duration=30)
    assert not result.ok
    assert result.auth_error
    assert calls["n"] == 1  # terminated immediately


def test_wait_rate_limit_stops_without_local_retry(tmp_path: Path, monkeypatch):
    """On Meta rate limit, stop immediately and trip the shared gate (no poll storm)."""
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
    pub = InstagramPublisher(make_config(), db, client=client)
    result = pub._wait_container_ready(
        client, "cid1", "token", is_video=True, interval=60.0, max_duration=300
    )
    assert not result.ok
    assert result.ambiguous
    assert result.retry_after and result.retry_after >= 60
    assert calls["n"] == 1
    assert pub.api_gate.blocked()


def test_wait_finished_after_in_progress(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    sleeps: list[float] = []
    monkeypatch.setattr(
        "azeroth_moments.publishers.instagram.time.sleep",
        lambda s: sleeps.append(s),
    )
    states = iter(
        [
            (200, {"status_code": "IN_PROGRESS"}),
            (200, {"status_code": "FINISHED"}),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        status, payload = next(states)
        return httpx.Response(status, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pub = InstagramPublisher(make_config(), db, client=client)
    result = pub._wait_container_ready(
        client, "cid1", "token", is_video=True, interval=60.0, max_duration=300
    )
    assert result.ok
    assert sleeps and sleeps[0] == 60.0


def test_reconcile_rate_limit_is_ambiguous_with_backoff(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")

    def handler(request: httpx.Request) -> httpx.Response:
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
    pub = InstagramPublisher(make_config(), db, client=client)
    result = pub.reconcile(["111", "222"], "")
    assert not result.ok
    assert result.ambiguous
    assert not result.auth_error
    assert result.retry_after and result.retry_after >= 60
    assert result.container_ids == ["111", "222"]
    assert pub.api_gate.blocked()


def test_reconcile_permission_403_fails_without_retry_loop(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "message": "Application does not have permission for this action",
                    "type": "OAuthException",
                    "code": 10,
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pub = InstagramPublisher(make_config(), db, client=client)
    result = pub.reconcile(["222"], "")
    assert not result.ok
    assert result.auth_error
    assert not result.ambiguous


def test_worker_skips_unknown_before_next_retry(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    sid = db.insert_screenshot(
        path="/tmp/a.png",
        sha256="hash1",
        author="babunigaming",
        width=100,
        height=100,
        aspect=1.0,
        shape="square",
        ingest_reason="new",
    )
    db.update_publication(
        sid,
        "instagram",
        status="unknown",
        last_error="rate_limit: test",
        container_ids=["cid"],
        next_retry_at=time.time() + 3600,
    )

    calls = {"n": 0}

    class FakeIG:
        def reconcile(self, container_ids, remote_id):
            calls["n"] += 1
            raise AssertionError("should not be called while next_retry_at is future")

    worker = Worker(make_config(tmp_path, instagram_enabled=True), db)
    worker.instagram = FakeIG()  # type: ignore[assignment]
    worked = worker._reconcile_unknowns()
    assert worked is False
    assert calls["n"] == 0
    pub = db.get_publication(sid, "instagram")
    assert pub is not None
    assert pub.status == "unknown"
    assert json.loads(pub.container_ids) == ["cid"]
