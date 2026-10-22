from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from azeroth_moments.config import DEFAULT_CAPTION, Config

# Far-future default so Instagram publishers do not auto-refresh mid-test.
_DEFAULT_TOKEN_EXPIRES = (
    datetime.now(timezone.utc) + timedelta(days=45)
).isoformat()


def make_config(tmp_path: Path | None = None, **overrides) -> Config:
    base = dict(
        watch_directory=tmp_path or Path("/tmp"),
        author="babunigaming",
        default_caption=DEFAULT_CAPTION,
        discord_caption="",
        instagram_caption="",
        discord_enabled=True,
        discord_webhook_url="https://discord.com/api/webhooks/1/abc",
        discord_max_attachment_bytes=10_000_000,
        instagram_enabled=True,
        instagram_login_app_id="",
        instagram_login_app_secret="",
        instagram_access_token="token",
        instagram_account_id="1",
        instagram_api_version="v22.0",
        instagram_token_expires_at=_DEFAULT_TOKEN_EXPIRES,
        instagram_staging_provider="s3",
        instagram_landscape_mode="carousel",
        instagram_carousel_min_panels=2,
        instagram_carousel_max_panels=4,
        instagram_carousel_default_panels=3,
        instagram_carousel_overlap_percent=5.0,
        instagram_canvas_mode="adaptive",
        instagram_padding_style="blurred_background",
        instagram_single_image_fallback=True,
        publish_existing_on_startup=False,
        publish_max_per_hour=10,
        aws_access_key_id="x",
        aws_secret_access_key="y",
        aws_region="auto",
        s3_bucket="bucket",
        s3_endpoint_url="",
        s3_staging_retention_seconds=3600,
        video_enabled=True,
        instagram_video_enabled=True,
        discord_video_enabled=True,
        instagram_video_landscape_mode="preview_carousel",
        instagram_video_preview_mode="center",
        instagram_video_carousel_panels=3,
        instagram_video_include_preview_first=True,
        instagram_video_target_aspect_ratio="9:16",
        instagram_video_max_duration_seconds=0.0,
        instagram_video_transcode_preset="balanced",
        desktop_notifications=False,
        dry_run=False,
        log_level="INFO",
    )
    base.update(overrides)
    return Config(**base)
