"""FFmpeg / ffprobe helpers."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    pass


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def require_ffmpeg() -> None:
    if not ffmpeg_available():
        raise FFmpegError(
            "ffmpeg/ffprobe not found. Install FFmpeg or disable VIDEO_ENABLED."
        )


@dataclass(frozen=True)
class VideoProbe:
    width: int
    height: int
    duration: float
    fps: float
    has_audio: bool
    video_codec: str
    audio_codec: str


def probe_video(path: Path) -> VideoProbe:
    require_ffmpeg()
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise FFmpegError(f"ffprobe failed for {path.name}: {exc}") from exc
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
    astream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not vstream:
        raise FFmpegError(f"No video stream in {path.name}")
    width = int(vstream.get("width") or 0)
    height = int(vstream.get("height") or 0)
    if width < 16 or height < 16:
        raise FFmpegError(f"Video too small: {width}x{height}")
    duration = float(data.get("format", {}).get("duration") or vstream.get("duration") or 0)
    fps = _parse_fps(vstream.get("avg_frame_rate") or vstream.get("r_frame_rate") or "30/1")
    return VideoProbe(
        width=width,
        height=height,
        duration=duration,
        fps=fps,
        has_audio=astream is not None,
        video_codec=str(vstream.get("codec_name") or ""),
        audio_codec=str((astream or {}).get("codec_name") or ""),
    )


def _parse_fps(raw: str) -> float:
    if "/" in raw:
        num, den = raw.split("/", 1)
        try:
            den_f = float(den)
            if den_f == 0:
                return 30.0
            return float(num) / den_f
        except ValueError:
            return 30.0
    try:
        return float(raw)
    except ValueError:
        return 30.0


def run_ffmpeg(args: list[str], *, timeout: float = 600.0) -> None:
    require_ffmpeg()
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    log.debug("ffmpeg %s", " ".join(cmd[4:]))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError("ffmpeg timed out") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:500]
        raise FFmpegError(f"ffmpeg failed: {err}")
