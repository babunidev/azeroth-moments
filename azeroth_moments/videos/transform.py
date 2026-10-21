"""Prepare Instagram video derivatives (single or preview-first carousel)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from azeroth_moments.config import Config
from azeroth_moments.images.classify import ImageMetrics, classify_shape
from azeroth_moments.paths import derivatives_dir
from azeroth_moments.videos.decide import InstagramVideoDecision, decide_instagram_video_mode
from azeroth_moments.videos.ffmpeg_tools import (
    FFmpegError,
    VideoProbe,
    ffmpeg_available,
    probe_video,
    run_ffmpeg,
)

log = logging.getLogger(__name__)

# Instagram-friendly portrait video canvas (9:16)
TARGET_W = 1080
TARGET_H = 1920


@dataclass
class PreparedInstagramVideoAssets:
    mode: str
    paths: list[Path]
    metrics: ImageMetrics
    decision: InstagramVideoDecision
    probe: VideoProbe
    panel_count: int
    panel_labels: list[str] = field(default_factory=list)
    panel_output_dims: list[tuple[int, int]] = field(default_factory=list)
    validation_ok: bool = False
    decision_summary: str = ""
    errors: list[str] = field(default_factory=list)
    media_kind: str = "video"


def _parse_aspect(raw: str) -> tuple[int, int]:
    text = (raw or "9:16").strip()
    if ":" in text:
        a, b = text.split(":", 1)
        return max(1, int(a)), max(1, int(b))
    return 9, 16


def _target_size(config: Config) -> tuple[int, int]:
    aw, ah = _parse_aspect(config.instagram_video_target_aspect_ratio)
    # Normalize to ~1080 on the shorter side for portrait
    if aw <= ah:
        h = TARGET_H
        w = int(round(h * aw / ah))
        # force even
        w -= w % 2
        h -= h % 2
        return max(2, w), max(2, h)
    w = TARGET_W
    h = int(round(w * ah / aw))
    w -= w % 2
    h -= h % 2
    return max(2, w), max(2, h)


def _preset_flags(preset: str) -> list[str]:
    p = (preset or "balanced").lower()
    if p == "fast":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26"]
    if p == "quality":
        return ["-c:v", "libx264", "-preset", "slow", "-crf", "20"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "23"]


def _encode_clip(
    source: Path,
    dest: Path,
    *,
    vf: str,
    probe: VideoProbe,
    config: Config,
) -> None:
    args: list[str] = ["-i", str(source)]
    max_dur = config.instagram_video_max_duration_seconds
    if max_dur and max_dur > 0:
        args.extend(["-t", str(max_dur)])
    args.extend(["-vf", vf, *_preset_flags(config.instagram_video_transcode_preset)])
    args.extend(["-pix_fmt", "yuv420p", "-movflags", "+faststart"])
    if probe.has_audio:
        args.extend(["-c:a", "aac", "-b:a", "128k", "-ac", "2"])
    else:
        args.append("-an")
    # Cap fps lightly if absurdly high
    if probe.fps > 60:
        args.extend(["-r", "30"])
    args.append(str(dest))
    run_ffmpeg(args)


def _crop_windows(width: int, height: int, aspect_w: int, aspect_h: int) -> dict[str, tuple[int, int, int, int]]:
    """Return named crops as (cw, ch, x, y) covering full height when possible."""
    # Ideal 9:16 window using full source height
    crop_h = height
    crop_w = int(round(crop_h * aspect_w / aspect_h))
    if crop_w > width:
        crop_w = width
        crop_h = int(round(crop_w * aspect_h / aspect_w))
        y0 = max(0, (height - crop_h) // 2)
    else:
        y0 = 0
    crop_w = max(2, crop_w - (crop_w % 2))
    crop_h = max(2, crop_h - (crop_h % 2))
    left_x = 0
    right_x = max(0, width - crop_w)
    mid_x = max(0, (width - crop_w) // 2)

    # Slightly wider preview window, then scaled/cropped to target later via scale+crop
    preview_w = min(width, int(round(crop_w * 1.15)))
    preview_w = max(2, preview_w - (preview_w % 2))
    preview_x = max(0, (width - preview_w) // 2)

    return {
        "preview": (preview_w, crop_h if crop_w <= width else crop_h, preview_x, y0),
        "left": (crop_w, crop_h, left_x, y0),
        "middle": (crop_w, crop_h, mid_x, y0),
        "right": (crop_w, crop_h, right_x, y0),
    }


def _vf_for_crop(cw: int, ch: int, x: int, y: int, tw: int, th: int) -> str:
    return (
        f"crop={cw}:{ch}:{x}:{y},"
        f"scale={tw}:{th}:force_original_aspect_ratio=increase,"
        f"crop={tw}:{th}"
    )


def _vf_fit_single(tw: int, th: int) -> str:
    # Letterbox/pillarbox with blurred background via split is complex; use pad with dark fill
    # for unusual ratios while preserving composition (no stretch).
    return (
        f"scale={tw}:{th}:force_original_aspect_ratio=decrease,"
        f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color=0x141418"
    )


def prepare_instagram_video_assets(
    source_path: Path,
    config: Config,
) -> PreparedInstagramVideoAssets:
    if not config.video_enabled or not config.instagram_video_enabled:
        return PreparedInstagramVideoAssets(
            mode="disabled",
            paths=[],
            metrics=ImageMetrics(0, 0, 0.0, "irregular"),
            decision=InstagramVideoDecision("single", "video disabled"),
            probe=VideoProbe(0, 0, 0.0, 0.0, False, "", ""),
            panel_count=0,
            validation_ok=False,
            decision_summary="video disabled",
            errors=["video processing disabled"],
        )
    if not ffmpeg_available():
        return PreparedInstagramVideoAssets(
            mode="error",
            paths=[],
            metrics=ImageMetrics(0, 0, 0.0, "irregular"),
            decision=InstagramVideoDecision("single", "ffmpeg missing"),
            probe=VideoProbe(0, 0, 0.0, 0.0, False, "", ""),
            panel_count=0,
            validation_ok=False,
            decision_summary="ffmpeg missing",
            errors=["ffmpeg/ffprobe not available"],
        )

    try:
        probe = probe_video(source_path)
    except FFmpegError as exc:
        return PreparedInstagramVideoAssets(
            mode="error",
            paths=[],
            metrics=ImageMetrics(0, 0, 0.0, "irregular"),
            decision=InstagramVideoDecision("single", "probe failed"),
            probe=VideoProbe(0, 0, 0.0, 0.0, False, "", ""),
            panel_count=0,
            validation_ok=False,
            decision_summary=str(exc),
            errors=[str(exc)],
        )

    metrics = classify_shape(probe.width, probe.height)
    decision = decide_instagram_video_mode(metrics, config)
    tw, th = _target_size(config)
    work_dir = derivatives_dir() / f"vid_{source_path.stem}_{uuid.uuid4().hex[:10]}"
    work_dir.mkdir(parents=True, exist_ok=True)
    aw, ah = _parse_aspect(config.instagram_video_target_aspect_ratio)
    summary = [decision.reason]
    paths: list[Path] = []
    labels: list[str] = []

    try:
        if decision.mode == "preview_carousel":
            windows = _crop_windows(probe.width, probe.height, aw, ah)
            order = ["preview", "left", "middle", "right"]
            if not config.instagram_video_include_preview_first:
                order = ["left", "middle", "right"]
            # Enforce panel count for L/M/R (always 3 when using carousel)
            panels = max(2, min(4, config.instagram_video_carousel_panels))
            if panels != 3:
                # Still produce left/middle/right geometry; count is informational
                summary.append(f"carousel_panels_config={panels}")
            for label in order:
                cw, ch, x, y = windows[label]
                if cw < 2 or ch < 2:
                    raise FFmpegError(f"Invalid crop window for {label}")
                dest = work_dir / f"{label}.mp4"
                vf = _vf_for_crop(cw, ch, x, y, tw, th)
                _encode_clip(source_path, dest, vf=vf, probe=probe, config=config)
                if dest.stat().st_size < 1000:
                    raise FFmpegError(f"Empty/invalid output for {label}")
                paths.append(dest)
                labels.append(label)
            summary.append(f"preview_carousel clips={len(paths)} order={','.join(labels)}")
            mode = "preview_carousel"
        else:
            dest = work_dir / "single.mp4"
            vf = _vf_fit_single(tw, th)
            _encode_clip(source_path, dest, vf=vf, probe=probe, config=config)
            if dest.stat().st_size < 1000:
                raise FFmpegError("Empty/invalid single video output")
            paths = [dest]
            labels = ["single"]
            mode = "single"
            summary.append("single video")

        dims = [(tw, th) for _ in paths]
        # Incomplete carousel guard
        if mode == "preview_carousel":
            expected = 4 if config.instagram_video_include_preview_first else 3
            if len(paths) != expected:
                raise FFmpegError(
                    f"Incomplete carousel set: got {len(paths)} expected {expected}"
                )

        log.info(
            "Prepared Instagram video assets for %s mode=%s clips=%s",
            source_path.name,
            mode,
            len(paths),
        )
        return PreparedInstagramVideoAssets(
            mode=mode,
            paths=paths,
            metrics=metrics,
            decision=decision,
            probe=probe,
            panel_count=len(paths),
            panel_labels=labels,
            panel_output_dims=dims,
            validation_ok=True,
            decision_summary="; ".join(summary),
            errors=[],
        )
    except Exception as exc:  # noqa: BLE001
        log.error("Video preparation failed for %s: %s", source_path, exc)
        return PreparedInstagramVideoAssets(
            mode=decision.mode,
            paths=[],
            metrics=metrics,
            decision=decision,
            probe=probe,
            panel_count=0,
            panel_labels=[],
            panel_output_dims=[],
            validation_ok=False,
            decision_summary="; ".join(summary + [str(exc)]),
            errors=[str(exc)],
        )
