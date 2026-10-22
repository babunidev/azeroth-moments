from __future__ import annotations

from pathlib import Path

from azeroth_moments.notify import notify_desktop, notify_published


def test_notify_disabled_is_noop(monkeypatch):
    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("should not run")

    monkeypatch.setattr("azeroth_moments.notify.subprocess.run", boom)
    assert notify_desktop("t", "b", enabled=False) is False
    assert notify_published("discord", filename="a.png", enabled=False) is False
    assert notify_published("instagram", filename="a.png", dry_run=True) is False
    assert called["n"] == 0


def test_notify_published_invokes_notify_send(monkeypatch):
    seen: dict[str, object] = {}

    def fake_which(name: str) -> str | None:
        return "/usr/bin/notify-send" if name == "notify-send" else None

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd

        class R:
            returncode = 0
            stderr = ""
            stdout = ""

        return R()

    monkeypatch.setattr("azeroth_moments.notify.shutil.which", fake_which)
    monkeypatch.setattr("azeroth_moments.notify.subprocess.run", fake_run)
    assert notify_published(
        "discord",
        filename=str(Path("/tmp/?hello+azeroth.png")),
        media_type="image",
        enabled=True,
    )
    cmd = seen["cmd"]
    assert isinstance(cmd, list)
    assert cmd[0] == "/usr/bin/notify-send"
    assert "Posted to Discord" in cmd
    assert any("?hello+azeroth.png" in str(part) for part in cmd)


def test_notify_missing_binary(monkeypatch):
    monkeypatch.setattr("azeroth_moments.notify.shutil.which", lambda _n: None)
    assert notify_desktop("t", "b", enabled=True) is False
