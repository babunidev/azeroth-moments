"""Configuration loading and validation."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from azeroth_moments.captions import DEFAULT_HASHTAGS, DEFAULT_TAGLINE, build_caption
from azeroth_moments.paths import default_env_candidates, expand_user_path

log = logging.getLogger(__name__)

DEFAULT_CAPTION = f"{DEFAULT_TAGLINE}\n\n{DEFAULT_HASHTAGS}"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_str(name: str, default: str = "") -> str:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw


@dataclass(frozen=True)
class Config:
    watch_directory: Path
    author: str
    default_caption: str
    discord_caption: str
    instagram_caption: str
    discord_enabled: bool
    discord_webhook_url: str
    discord_max_attachment_bytes: int
    instagram_enabled: bool
    instagram_login_app_id: str
    instagram_login_app_secret: str
    instagram_access_token: str
    instagram_account_id: str
    instagram_api_version: str
    instagram_token_expires_at: str
    instagram_staging_provider: str
    instagram_landscape_mode: str
    instagram_carousel_min_panels: int
    instagram_carousel_max_panels: int
    instagram_carousel_default_panels: int
    instagram_carousel_overlap_percent: float
    instagram_canvas_mode: str
    instagram_padding_style: str
    instagram_single_image_fallback: bool
    publish_existing_on_startup: bool
    publish_max_per_hour: int
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str
    s3_bucket: str
    s3_endpoint_url: str
    s3_staging_retention_seconds: int
    video_enabled: bool
    instagram_video_enabled: bool
    discord_video_enabled: bool
    instagram_video_landscape_mode: str
    instagram_video_preview_mode: str
    instagram_video_carousel_panels: int
    instagram_video_include_preview_first: bool
    instagram_video_target_aspect_ratio: str
    instagram_video_max_duration_seconds: float
    instagram_video_transcode_preset: str
    desktop_notifications: bool
    dry_run: bool
    log_level: str

    def caption_for(self, platform: str, path: Path | str | None = None) -> str:
        """Return the caption for *platform*, optionally derived from *path*.

        When *path* is set, a leading ``?`` in the filename inserts a custom
        middle line (``+`` → space). Platform-specific caption overrides still
        apply as the tagline/hashtag base.
        """
        if platform == "discord" and self.discord_caption.strip():
            base = self.discord_caption
        elif platform == "instagram" and self.instagram_caption.strip():
            base = self.instagram_caption
        else:
            base = self.default_caption
        if path is None:
            return base
        return build_caption(path, base_caption=base)

    def instagram_configured(self) -> tuple[bool, str]:
        """Local credential/staging checks only — does not call Meta."""
        if not self.instagram_enabled:
            return False, "Instagram disabled"
        if not self.instagram_access_token:
            return False, "INSTAGRAM_ACCESS_TOKEN missing"
        if not self.instagram_account_id:
            return False, "INSTAGRAM_ACCOUNT_ID missing"
        if self.instagram_staging_provider != "s3":
            return False, f"Unsupported staging provider: {self.instagram_staging_provider}"
        if not self.s3_bucket:
            return False, "S3_BUCKET missing for Instagram staging"
        if not self.aws_access_key_id or not self.aws_secret_access_key:
            return False, "AWS credentials missing for Instagram staging"
        return True, "ok"

    def instagram_ready(self) -> tuple[bool, str]:
        """Backward-compatible alias for local configuration readiness."""
        return self.instagram_configured()

    def discord_ready(self) -> tuple[bool, str]:
        if not self.discord_enabled:
            return False, "Discord disabled"
        if not self.discord_webhook_url:
            return False, "DISCORD_WEBHOOK_URL missing"
        return True, "ok"

    def video_ingest_enabled(self) -> bool:
        return self.video_enabled and (
            self.discord_video_enabled or self.instagram_video_enabled
        )


def load_dotenv_files(explicit: Path | None = None) -> Path | None:
    if explicit is not None:
        load_dotenv(explicit, override=True)
        return explicit
    for candidate in default_env_candidates():
        if candidate.is_file():
            load_dotenv(candidate, override=True)
            return candidate
    load_dotenv(override=False)
    return None


def load_config(explicit_env: Path | None = None, dry_run: bool | None = None) -> Config:
    loaded = load_dotenv_files(explicit_env)
    if loaded:
        log.debug("Loaded config from %s", loaded)

    watch = expand_user_path(_env_str("WATCH_DIRECTORY", "~/Pictures/Azeroth Moments"))
    cfg = Config(
        watch_directory=watch,
        author=_env_str("AUTHOR", "babunigaming"),
        default_caption=_env_str("DEFAULT_CAPTION", DEFAULT_CAPTION).replace("\\n", "\n"),
        discord_caption=_env_str("DISCORD_CAPTION", "").replace("\\n", "\n"),
        instagram_caption=_env_str("INSTAGRAM_CAPTION", "").replace("\\n", "\n"),
        discord_enabled=_env_bool("DISCORD_ENABLED", True),
        discord_webhook_url=_env_str("DISCORD_WEBHOOK_URL", ""),
        discord_max_attachment_bytes=_env_int("DISCORD_MAX_ATTACHMENT_BYTES", 10 * 1024 * 1024),
        instagram_enabled=_env_bool("INSTAGRAM_ENABLED", False),
        instagram_login_app_id=_env_str("INSTAGRAM_LOGIN_APP_ID", ""),
        instagram_login_app_secret=_env_str("INSTAGRAM_LOGIN_APP_SECRET", ""),
        instagram_access_token=_env_str("INSTAGRAM_ACCESS_TOKEN", ""),
        instagram_account_id=_env_str("INSTAGRAM_ACCOUNT_ID", ""),
        instagram_api_version=_env_str("INSTAGRAM_API_VERSION", "v22.0"),
        instagram_token_expires_at=_env_str("INSTAGRAM_TOKEN_EXPIRES_AT", ""),
        instagram_staging_provider=_env_str("INSTAGRAM_STAGING_PROVIDER", "s3"),
        instagram_landscape_mode=_env_str("INSTAGRAM_LANDSCAPE_MODE", "carousel"),
        instagram_carousel_min_panels=_env_int("INSTAGRAM_CAROUSEL_MIN_PANELS", 2),
        instagram_carousel_max_panels=_env_int("INSTAGRAM_CAROUSEL_MAX_PANELS", 4),
        instagram_carousel_default_panels=_env_int("INSTAGRAM_CAROUSEL_DEFAULT_PANELS", 3),
        instagram_carousel_overlap_percent=float(
            _env_str("INSTAGRAM_CAROUSEL_OVERLAP_PERCENT", "5")
        ),
        instagram_canvas_mode=_env_str("INSTAGRAM_CANVAS_MODE", "adaptive"),
        instagram_padding_style=_env_str("INSTAGRAM_PADDING_STYLE", "blurred_background"),
        instagram_single_image_fallback=_env_bool("INSTAGRAM_SINGLE_IMAGE_FALLBACK", True),
        publish_existing_on_startup=_env_bool("PUBLISH_EXISTING_ON_STARTUP", False),
        publish_max_per_hour=_env_int("PUBLISH_MAX_PER_HOUR", 10),
        aws_access_key_id=_env_str("AWS_ACCESS_KEY_ID", ""),
        aws_secret_access_key=_env_str("AWS_SECRET_ACCESS_KEY", ""),
        aws_region=_env_str("AWS_REGION", "auto"),
        s3_bucket=_env_str("S3_BUCKET", ""),
        s3_endpoint_url=_env_str("S3_ENDPOINT_URL", ""),
        s3_staging_retention_seconds=_env_int("S3_STAGING_RETENTION_SECONDS", 3600),
        video_enabled=_env_bool("VIDEO_ENABLED", True),
        instagram_video_enabled=_env_bool("INSTAGRAM_VIDEO_ENABLED", True),
        discord_video_enabled=_env_bool("DISCORD_VIDEO_ENABLED", True),
        instagram_video_landscape_mode=_env_str(
            "INSTAGRAM_VIDEO_LANDSCAPE_MODE", "preview_carousel"
        ),
        instagram_video_preview_mode=_env_str("INSTAGRAM_VIDEO_PREVIEW_MODE", "center"),
        instagram_video_carousel_panels=_env_int("INSTAGRAM_VIDEO_CAROUSEL_PANELS", 3),
        instagram_video_include_preview_first=_env_bool(
            "INSTAGRAM_VIDEO_INCLUDE_PREVIEW_FIRST", True
        ),
        instagram_video_target_aspect_ratio=_env_str(
            "INSTAGRAM_VIDEO_TARGET_ASPECT_RATIO", "9:16"
        ),
        instagram_video_max_duration_seconds=float(
            _env_str("INSTAGRAM_VIDEO_MAX_DURATION_SECONDS", "0") or "0"
        ),
        instagram_video_transcode_preset=_env_str(
            "INSTAGRAM_VIDEO_TRANSCODE_PRESET", "balanced"
        ),
        desktop_notifications=_env_bool("DESKTOP_NOTIFICATIONS", True),
        dry_run=_env_bool("DRY_RUN", False) if dry_run is None else dry_run,
        log_level=_env_str("LOG_LEVEL", "INFO"),
    )
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    if cfg.instagram_carousel_min_panels < 2:
        raise ValueError("INSTAGRAM_CAROUSEL_MIN_PANELS must be >= 2")
    if cfg.instagram_carousel_max_panels < cfg.instagram_carousel_min_panels:
        raise ValueError("INSTAGRAM_CAROUSEL_MAX_PANELS must be >= MIN")
    if cfg.instagram_carousel_max_panels > 10:
        raise ValueError("INSTAGRAM_CAROUSEL_MAX_PANELS must be <= 10")
    if not (0 <= cfg.instagram_carousel_overlap_percent < 50):
        raise ValueError("INSTAGRAM_CAROUSEL_OVERLAP_PERCENT must be in [0, 50)")
    if cfg.publish_max_per_hour < 1:
        raise ValueError("PUBLISH_MAX_PER_HOUR must be >= 1")
    mode = cfg.instagram_landscape_mode.lower()
    if mode not in {"carousel", "single", "adaptive"}:
        raise ValueError("INSTAGRAM_LANDSCAPE_MODE must be carousel|single|adaptive")
    vmode = cfg.instagram_video_landscape_mode.lower()
    if vmode not in {"single", "preview_carousel", "adaptive"}:
        raise ValueError(
            "INSTAGRAM_VIDEO_LANDSCAPE_MODE must be single|preview_carousel|adaptive"
        )
    if cfg.instagram_video_carousel_panels < 2 or cfg.instagram_video_carousel_panels > 4:
        raise ValueError("INSTAGRAM_VIDEO_CAROUSEL_PANELS must be in [2, 4]")
    if cfg.instagram_video_preview_mode.lower() not in {"center"}:
        raise ValueError("INSTAGRAM_VIDEO_PREVIEW_MODE must be center")
    if cfg.instagram_video_transcode_preset.lower() not in {"fast", "balanced", "quality"}:
        raise ValueError("INSTAGRAM_VIDEO_TRANSCODE_PRESET must be fast|balanced|quality")
