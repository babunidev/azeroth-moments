"""Desktop notifications for successful Discord / Instagram publishes."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

APP_NAME = "Azeroth Moments"


def notify_desktop(
    title: str,
    body: str,
    *,
    urgency: str = "normal",
    icon: str = "camera-photo",
    enabled: bool = True,
) -> bool:
    """Send a Freedesktop notification via ``notify-send`` (KDE/GNOME/etc.).

    Returns True if a notification was dispatched. Failures are logged and
    never raise — publishing must not depend on the notification daemon.
    """
    if not enabled:
        return False
    binary = shutil.which("notify-send")
    if not binary:
        log.debug("notify-send not found; skipping desktop notification")
        return False
    cmd = [
        binary,
        f"--app-name={APP_NAME}",
        f"--urgency={urgency}",
        f"--icon={icon}",
        "--expire-time=8000",
        title,
        body,
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("Desktop notification failed: %s", exc)
        return False
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        log.warning(
            "Desktop notification failed (exit %s): %s",
            completed.returncode,
            err or "unknown error",
        )
        return False
    return True


def notify_published(
    platform: str,
    *,
    filename: str,
    media_type: str = "image",
    enabled: bool = True,
    dry_run: bool = False,
) -> bool:
    """Notify that a file was published to Discord or Instagram."""
    if dry_run or not enabled:
        return False
    kind = "video" if media_type == "video" else "image"
    label = "Discord" if platform == "discord" else "Instagram"
    name = Path(filename).name if filename else "unknown file"
    icon = "folder-video" if kind == "video" else "camera-photo"
    return notify_desktop(
        f"Posted to {label}",
        f"{name}\n({kind})",
        icon=icon,
        enabled=True,
    )
