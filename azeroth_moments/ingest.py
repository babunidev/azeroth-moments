"""Media ingest: hashing, validation, DB registration for images and videos."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from azeroth_moments.config import Config
from azeroth_moments.db import Database
from azeroth_moments.images.classify import classify_shape
from azeroth_moments.videos.ffmpeg_tools import FFmpegError, ffmpeg_available, probe_video

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv"}
SUPPORTED_SUFFIXES = IMAGE_SUFFIXES | VIDEO_SUFFIXES


def media_kind_for(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    return None


def is_candidate_file(path: Path, config: Config | None = None) -> bool:
    name = path.name
    if not path.is_file():
        return False
    if name.startswith(".") or name.startswith("~$") or name.endswith("~"):
        return False
    if name.lower().endswith((".tmp", ".part", ".crdownload")):
        return False
    kind = media_kind_for(path)
    if kind is None:
        return False
    if kind == "video":
        if config is not None and not config.video_ingest_enabled():
            return False
    return True


def wait_until_stable(
    path: Path,
    *,
    checks: int = 3,
    interval: float = 0.4,
    timeout: float = 120.0,
) -> bool:
    """Wait until file size stops changing and is readable."""
    deadline = time.time() + timeout
    last_size = -1
    stable = 0
    while time.time() < deadline:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size > 0 and size == last_size:
            stable += 1
            if stable >= checks:
                return True
        else:
            stable = 0
            last_size = size
        time.sleep(interval)
    return False


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def probe_image(path: Path) -> tuple[int, int, float, str]:
    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            width, height = im.size
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"Invalid image: {exc}") from exc
    if width < 32 or height < 32:
        raise ValueError(f"Image too small: {width}x{height}")
    metrics = classify_shape(width, height)
    return width, height, metrics.aspect_ratio, metrics.shape


def probe_media(path: Path, kind: str) -> tuple[int, int, float, str]:
    if kind == "image":
        return probe_image(path)
    if kind == "video":
        if not ffmpeg_available():
            raise ValueError("Video file requires ffmpeg/ffprobe")
        try:
            probe = probe_video(path)
        except FFmpegError as exc:
            raise ValueError(str(exc)) from exc
        metrics = classify_shape(probe.width, probe.height)
        return probe.width, probe.height, metrics.aspect_ratio, metrics.shape
    raise ValueError(f"Unsupported media kind: {kind}")


def register_screenshot(
    db: Database,
    config: Config,
    path: Path,
    *,
    ingest_reason: str,
) -> int | None:
    """Register a media file if new. Returns id or None if duplicate/rejected."""
    kind = media_kind_for(path)
    if kind is None:
        return None
    if kind == "video" and not config.video_ingest_enabled():
        log.info("Skipping video (disabled): %s", path.name)
        return None

    # Longer wait for large video copies
    timeout = 180.0 if kind == "video" else 30.0
    if not wait_until_stable(path, timeout=timeout):
        log.warning("File never stabilized: %s", path)
        return None
    try:
        width, height, aspect, shape = probe_media(path, kind)
        digest = sha256_file(path)
    except ValueError as exc:
        log.error("Rejecting %s: %s", path, exc)
        return None

    existing = db.get_by_sha(digest)
    if existing is not None:
        log.info("Duplicate hash skipped: %s (%s)", path.name, digest[:12])
        return None

    screenshot_id = db.insert_screenshot(
        path=str(path.resolve()),
        sha256=digest,
        author=config.author,
        width=width,
        height=height,
        aspect=aspect,
        shape=shape,
        ingest_reason=ingest_reason,
        media_type=kind,
    )
    log.info(
        "Registered %s id=%s reason=%s shape=%s %sx%s %s",
        kind,
        screenshot_id,
        ingest_reason,
        shape,
        width,
        height,
        path.name,
    )
    return screenshot_id
