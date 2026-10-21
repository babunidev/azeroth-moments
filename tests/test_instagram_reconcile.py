from pathlib import Path

import httpx

from azeroth_moments.db import Database
from azeroth_moments.publishers.instagram import InstagramPublisher
from tests.conftest import make_config


def test_reconcile_keeps_ambiguous(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    db = Database(tmp_path / "db.sqlite")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "c1", "status_code": "IN_PROGRESS"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pub = InstagramPublisher(make_config(), db, client=client, staging=None)
    result = pub.reconcile(["c1"], "")
    assert not result.ok
    assert result.ambiguous
    assert result.retry_after and result.retry_after >= 60
    assert result.container_ids == ["c1"]
