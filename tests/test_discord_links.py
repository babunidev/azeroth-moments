from __future__ import annotations

import time
from pathlib import Path

import httpx

from azeroth_moments.db import Database
from azeroth_moments.discord_links import (
    InstagramDiscordLinkAnnouncer,
    format_video_link_message,
)
from azeroth_moments.meta_api_gate import InstagramApiGate
from azeroth_moments.publishers.discord import DiscordPublisher
from azeroth_moments.publishers.instagram import InstagramPublisher
from azeroth_moments.worker import Worker
from tests.conftest import make_config


def _seed_video(
    db: Database,
    tmp_path: Path,
    *,
    sha: str,
    ig_remote: str = "media-1",
    disc_status: str = "awaiting_instagram_link",
) -> int:
    path = tmp_path / f"{sha}.mp4"
    path.write_bytes(b"fake")
    sid = db.insert_screenshot(
        path=str(path),
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
        status="published",
        remote_id=ig_remote,
        last_error="",
    )
    db.update_publication(
        sid,
        "discord",
        status=disc_status,
        last_error="oversized: too big" if disc_status != "published" else "",
    )
    return sid


def test_format_message():
    text = format_video_link_message(
        author="babunigaming",
        permalink="https://www.instagram.com/reel/EXAMPLE/",
    )
    assert "New video from BabuniGaming!" in text
    assert "https://www.instagram.com/reel/EXAMPLE/" in text


def test_announce_fetches_permalink_and_posts_once(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    sid = _seed_video(db, tmp_path, sha="ann-1")
    calls = {"ig": 0, "discord": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "graph.instagram.com" in str(request.url):
            calls["ig"] += 1
            return httpx.Response(
                200,
                json={
                    "id": "media-1",
                    "permalink": "https://www.instagram.com/reel/ABC123/",
                    "media_type": "CAROUSEL_ALBUM",
                },
            )
        if "discord.com" in str(request.url):
            calls["discord"] += 1
            return httpx.Response(200, json={"id": "disc-msg-9"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = make_config(tmp_path)
    gate = InstagramApiGate(db)
    ig = InstagramPublisher(cfg, db, client=client, api_gate=gate)
    disc = DiscordPublisher(cfg, client=client)
    ann = InstagramDiscordLinkAnnouncer(
        cfg, db, instagram=ig, discord=disc, api_gate=gate
    )
    r1 = ann.announce_for(db.get_screenshot(sid), db.get_publication(sid, "instagram"))  # type: ignore[arg-type]
    assert r1.ok
    assert r1.permalink.endswith("/ABC123/")
    assert r1.discord_message_id == "disc-msg-9"
    assert db.get_publication(sid, "instagram").permalink.endswith("/ABC123/")  # type: ignore[union-attr]
    assert db.get_publication(sid, "discord").status == "published"  # type: ignore[union-attr]
    assert calls["discord"] == 1

    # Second call is deduped — no extra Discord post
    r2 = ann.announce_for(db.get_screenshot(sid), db.get_publication(sid, "instagram"))  # type: ignore[arg-type]
    assert r2.skipped
    assert calls["discord"] == 1


def test_announce_respects_cooldown(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    sid = _seed_video(db, tmp_path, sha="cool-1")
    gate = InstagramApiGate(db)
    gate.trip_rate_limit(detail="test", now=time.time())
    cfg = make_config(tmp_path)
    ann = InstagramDiscordLinkAnnouncer(cfg, db, api_gate=gate)
    result = ann.announce_for(
        db.get_screenshot(sid), db.get_publication(sid, "instagram")  # type: ignore[arg-type]
    )
    assert not result.ok
    assert "cooldown" in result.message
    row = db.get_link_announcement(sid)
    assert row is not None
    assert not row["discord_message_id"]


def test_announce_retries_missing_permalink(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    sid = _seed_video(db, tmp_path, sha="miss-1")
    states = iter(
        [
            httpx.Response(200, json={"id": "media-1", "media_type": "VIDEO"}),
            httpx.Response(
                200,
                json={
                    "id": "media-1",
                    "permalink": "https://www.instagram.com/reel/LATER/",
                    "media_type": "VIDEO",
                },
            ),
        ]
    )
    discord_posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "graph.instagram.com" in str(request.url):
            return next(states)
        discord_posts["n"] += 1
        return httpx.Response(200, json={"id": "d1"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = make_config(tmp_path)
    gate = InstagramApiGate(db)
    ig = InstagramPublisher(cfg, db, client=client, api_gate=gate)
    disc = DiscordPublisher(cfg, client=client)
    ann = InstagramDiscordLinkAnnouncer(
        cfg, db, instagram=ig, discord=disc, api_gate=gate
    )
    first = ann.announce_for(
        db.get_screenshot(sid), db.get_publication(sid, "instagram")  # type: ignore[arg-type]
    )
    assert not first.ok
    assert discord_posts["n"] == 0
    # Clear backoff for immediate retry
    db.upsert_link_announcement(sid, next_retry_at=0.0)
    second = ann.announce_for(
        db.get_screenshot(sid), db.get_publication(sid, "instagram")  # type: ignore[arg-type]
    )
    assert second.ok
    assert second.permalink.endswith("/LATER/")
    assert discord_posts["n"] == 1


def test_images_not_announced(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    path = tmp_path / "shot.png"
    path.write_bytes(b"x")
    sid = db.insert_screenshot(
        path=str(path),
        sha256="img-1",
        author="babunigaming",
        width=100,
        height=100,
        aspect=1.0,
        shape="square",
        ingest_reason="new",
        media_type="image",
    )
    db.update_publication(sid, "instagram", status="published", remote_id="m1")
    pending = db.list_pending_video_link_announcements()
    assert pending == []


def test_worker_oversized_video_awaits_link(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "t.db")
    path = tmp_path / "big.mp4"
    path.write_bytes(b"x" * 100)
    sid = db.insert_screenshot(
        path=str(path),
        sha256="big-1",
        author="babunigaming",
        width=1920,
        height=1080,
        aspect=16 / 9,
        shape="landscape",
        ingest_reason="new",
        media_type="video",
    )
    worker = Worker(make_config(tmp_path, discord_max_attachment_bytes=10), db)
    result = type(
        "R",
        (),
        {
            "ok": False,
            "error": "oversized: 100 bytes exceeds Discord limit 10",
            "ambiguous": False,
            "auth_error": False,
            "retry_after": None,
            "remote_id": "",
            "container_ids": None,
            "staged_keys": None,
        },
    )()
    worker._apply_result(sid, "discord", 0, result, media_type="video")
    pub = db.get_publication(sid, "discord")
    assert pub is not None
    assert pub.status == "awaiting_instagram_link"
