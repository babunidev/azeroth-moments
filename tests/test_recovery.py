from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from azeroth_moments.db import Database
from azeroth_moments.meta_api_gate import InstagramApiGate
from azeroth_moments.publishers.instagram import InstagramPublisher
from azeroth_moments.recovery import InstagramRecovery, RecoveryIntent
from azeroth_moments.worker import Worker
from tests.conftest import make_config


def _seed_unknown(db: Database, tmp_path: Path, *, sha: str = "rec-sha") -> int:
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"fake-video")
    sid = db.insert_screenshot(
        path=str(src),
        sha256=sha,
        author="babunigaming",
        width=1920,
        height=1080,
        aspect=16 / 9,
        shape="landscape",
        ingest_reason="new",
        media_type="video",
    )
    db.update_publication(
        sid,
        "instagram",
        status="unknown",
        last_error="rate_limit: test",
        container_ids=["c1", "c2", "parent"],
        next_retry_at=0.0,
    )
    return sid


def test_retry_queues_when_cooldown_active(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    sid = _seed_unknown(db, tmp_path)
    gate = InstagramApiGate(db)
    gate.trip_rate_limit(retry_after=600, detail="test", now=time.time())
    recovery = InstagramRecovery(make_config(tmp_path), db, api_gate=gate)
    report = recovery.retry(sid, platform="instagram")
    assert report.queued
    assert report.action == "queued_cooldown"
    assert report.cooldown_remaining > 0
    assert recovery.get_intent(sid) is not None
    pub = db.get_publication(sid, "instagram")
    assert pub is not None
    assert pub.status == "unknown"
    assert json.loads(pub.container_ids) == ["c1", "c2", "parent"]


def test_recover_finished_publishes_once(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    sid = _seed_unknown(db, tmp_path, sha="fin-sha")
    calls = {"publish": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET":
            return httpx.Response(
                200, json={"id": "parent", "status_code": "FINISHED", "status": "FINISHED"}
            )
        if path.endswith("/media_publish"):
            calls["publish"] += 1
            return httpx.Response(200, json={"id": "media-99"})
        return httpx.Response(400, json={"error": {"message": "unexpected", "code": 1}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gate = InstagramApiGate(db)
    pub = InstagramPublisher(make_config(tmp_path), db, client=client, api_gate=gate)
    recovery = InstagramRecovery(
        make_config(tmp_path), db, publisher=pub, api_gate=gate
    )
    report = recovery.retry(sid, platform="instagram")
    assert report.ok
    assert report.action == "published"
    assert report.status_after == "published"
    assert report.remote_id == "media-99"
    assert calls["publish"] == 1
    assert recovery.get_intent(sid) is None
    row = db.get_publication(sid, "instagram")
    assert row is not None and row.status == "published"


def test_recover_already_published_marks_sqlite(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    sid = _seed_unknown(db, tmp_path, sha="pub-sha")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"id": "parent", "status_code": "PUBLISHED", "status": "PUBLISHED"}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gate = InstagramApiGate(db)
    ig = InstagramPublisher(make_config(tmp_path), db, client=client, api_gate=gate)
    recovery = InstagramRecovery(
        make_config(tmp_path), db, publisher=ig, api_gate=gate
    )
    report = recovery.retry(sid, platform="instagram")
    assert report.action == "mark_published"
    assert db.get_publication(sid, "instagram").status == "published"  # type: ignore[union-attr]


def test_expired_requires_confirm_rebuild(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    sid = _seed_unknown(db, tmp_path, sha="exp-sha")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"id": "parent", "status_code": "EXPIRED", "status": "EXPIRED"}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gate = InstagramApiGate(db)
    ig = InstagramPublisher(make_config(tmp_path), db, client=client, api_gate=gate)
    recovery = InstagramRecovery(
        make_config(tmp_path), db, publisher=ig, api_gate=gate
    )
    report = recovery.retry(sid, platform="instagram")
    assert report.needs_confirm_rebuild
    assert report.action == "need_confirm_rebuild"
    assert db.get_publication(sid, "instagram").status == "unknown"  # type: ignore[union-attr]
    assert json.loads(db.get_publication(sid, "instagram").container_ids) == [  # type: ignore[union-attr]
        "c1",
        "c2",
        "parent",
    ]


def test_rate_limit_during_recovery_trips_gate(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    sid = _seed_unknown(db, tmp_path, sha="rl-sha")

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
    gate = InstagramApiGate(db)
    ig = InstagramPublisher(make_config(tmp_path), db, client=client, api_gate=gate)
    recovery = InstagramRecovery(
        make_config(tmp_path), db, publisher=ig, api_gate=gate
    )
    report = recovery.retry(sid, platform="instagram")
    assert report.action == "queued_cooldown"
    assert gate.blocked()
    assert recovery.get_intent(sid) is not None


def test_ambiguous_outcome_stays_unknown(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    sid = _seed_unknown(db, tmp_path, sha="amb-sha")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"id": "parent", "status_code": "WEIRD", "status": "???"}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gate = InstagramApiGate(db)
    ig = InstagramPublisher(make_config(tmp_path), db, client=client, api_gate=gate)
    recovery = InstagramRecovery(
        make_config(tmp_path), db, publisher=ig, api_gate=gate
    )
    report = recovery.retry(sid, platform="instagram")
    assert report.action == "ambiguous"
    assert db.get_publication(sid, "instagram").status == "unknown"  # type: ignore[union-attr]


def test_intent_survives_restart_via_control(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    sid = _seed_unknown(db, tmp_path, sha="rst-sha")
    gate = InstagramApiGate(db)
    gate.trip_rate_limit(detail="x", now=time.time())
    r1 = InstagramRecovery(make_config(tmp_path), db, api_gate=gate)
    r1.retry(sid, platform="instagram")
    # New instances (simulating restart) still see intent + cooldown
    db2 = Database(tmp_path / "t.db")
    gate2 = InstagramApiGate(db2)
    r2 = InstagramRecovery(make_config(tmp_path), db2, api_gate=gate2)
    assert gate2.blocked()
    assert r2.get_intent(sid) is not None
    reports = r2.process_queued()
    assert reports == []  # still cooling; stays queued


def test_discord_untouched_during_instagram_recovery(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    sid = _seed_unknown(db, tmp_path, sha="disc-sha")
    db.update_publication(sid, "discord", status="failed", last_error="oversized: x")
    gate = InstagramApiGate(db)
    gate.trip_rate_limit(detail="x", now=time.time())
    recovery = InstagramRecovery(make_config(tmp_path), db, api_gate=gate)
    recovery.retry(sid, platform="instagram")
    disc = db.get_publication(sid, "discord")
    assert disc is not None
    assert disc.status == "failed"
    assert disc.last_error.startswith("oversized:")


def test_duplicate_sha_still_skipped(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    from PIL import Image

    from azeroth_moments.ingest import register_screenshot, sha256_file

    db = Database(tmp_path / "t.db")
    cfg = make_config(tmp_path)
    src = tmp_path / "same.png"
    Image.new("RGB", (640, 640), color=(10, 20, 30)).save(src)
    a = register_screenshot(db, cfg, src, ingest_reason="new")
    copy = tmp_path / "renamed.png"
    copy.write_bytes(src.read_bytes())
    b = register_screenshot(db, cfg, copy, ingest_reason="new")
    assert a is not None
    assert b is None
    assert db.get_by_sha(sha256_file(copy)).id == a  # type: ignore[union-attr]


def test_worker_processes_recovery_after_cooldown(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    sid = _seed_unknown(db, tmp_path, sha="wrk-sha")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, json={"id": "parent", "status_code": "PUBLISHED", "status": "PUBLISHED"}
            )
        return httpx.Response(400, json={"error": {"message": "no", "code": 1}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gate = InstagramApiGate(db)
    # Intent present, cooldown cleared
    intent = RecoveryIntent(screenshot_id=sid, queued_at=time.time())
    InstagramRecovery(make_config(tmp_path), db, api_gate=gate).save_intent(intent)
    db.update_publication(sid, "instagram", next_retry_at=0.0)

    worker = Worker(make_config(tmp_path), db)
    worker.instagram_api_gate = gate
    ig = InstagramPublisher(make_config(tmp_path), db, client=client, api_gate=gate)
    worker.instagram = ig
    worker.instagram_recovery = InstagramRecovery(
        make_config(tmp_path), db, publisher=ig, api_gate=gate
    )

    class FakeAnnouncer:
        def announce_for(self, shot, ig_pub, *, now=None):
            from azeroth_moments.discord_links import AnnounceResult

            return AnnounceResult(
                screenshot_id=shot.id, ok=True, skipped=True, message="test-skip"
            )

    worker.link_announcer = FakeAnnouncer()  # type: ignore[assignment]
    assert worker._process_instagram_recoveries() is True
    assert db.get_publication(sid, "instagram").status == "published"  # type: ignore[union-attr]


def test_retry_discord_platform_refused(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    sid = _seed_unknown(db, tmp_path, sha="ref-sha")
    recovery = InstagramRecovery(make_config(tmp_path), db)
    report = recovery.retry(sid, platform="discord")
    assert report.action == "refused"
