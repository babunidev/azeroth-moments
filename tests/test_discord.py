from pathlib import Path

import httpx

from azeroth_moments.publishers.discord import DiscordPublisher
from tests.conftest import make_config


def test_oversized_fails_without_transform(tmp_path: Path):
    path = tmp_path / "big.png"
    path.write_bytes(b"x" * 2000)
    pub = DiscordPublisher(make_config(discord_max_attachment_bytes=1000))
    result = pub.publish(path, "caption")
    assert not result.ok
    assert result.error.startswith("oversized:")


def test_uploads_original_bytes(tmp_path: Path):
    path = tmp_path / "shot.png"
    payload = b"\x89PNG_ORIGINAL_BYTES_123"
    path.write_bytes(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content
        assert payload in body
        assert b"files[0]" in body or b'filename="shot.png"' in body
        return httpx.Response(200, json={"id": "99"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    pub = DiscordPublisher(make_config(discord_max_attachment_bytes=10_000_000), client=client)
    result = pub.publish(path, "hello")
    assert result.ok
    assert result.remote_id == "99"


def test_oversized_video_fails_discord_only(tmp_path: Path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"0" * 2000)
    pub = DiscordPublisher(make_config(discord_max_attachment_bytes=1000))
    result = pub.publish(path, "caption")
    assert not result.ok
    assert result.error.startswith("oversized:")
