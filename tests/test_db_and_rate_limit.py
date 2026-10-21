from pathlib import Path

from azeroth_moments.db import Database
from azeroth_moments.rate_limit import RateLimiter


def test_baseline_and_rate_limit(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    assert not db.baseline_done()
    db.mark_baseline_done()
    assert db.baseline_done()

    sid = db.insert_screenshot(
        path="/tmp/a.png",
        sha256="abc",
        author="babunigaming",
        width=100,
        height=100,
        aspect=1.0,
        shape="square",
        ingest_reason="baseline_skipped",
    )
    pub = db.get_publication(sid, "discord")
    assert pub is not None
    assert pub.status == "baseline_skipped"

    limiter = RateLimiter(db, max_per_hour=2)
    assert limiter.can_publish()
    db.record_publish_event("discord")
    db.record_publish_event("discord")
    assert not limiter.can_publish()


def test_claim_pending(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    sid = db.insert_screenshot(
        path="/tmp/b.png",
        sha256="def",
        author="babunigaming",
        width=200,
        height=100,
        aspect=2.0,
        shape="landscape",
        ingest_reason="new",
    )
    claimed = db.claim_next_work("discord")
    assert claimed is not None
    shot, pub = claimed
    assert shot.id == sid
    assert pub.status == "processing"
