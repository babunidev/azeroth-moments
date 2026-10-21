"""Logging helpers that redact secrets."""

from __future__ import annotations

import logging
import re
from typing import Iterable

from azeroth_moments.paths import log_path

_SECRET_PATTERNS = [
    re.compile(r"(discord\.com/api/webhooks/)[^\s\"']+", re.I),
    re.compile(r"(access_token=)[^&\s\"']+", re.I),
    re.compile(r"(Authorization:\s*Bearer\s+)\S+", re.I),
    re.compile(r"(AWS_SECRET_ACCESS_KEY=)\S+", re.I),
    re.compile(r"(INSTAGRAM_ACCESS_TOKEN=)\S+", re.I),
    re.compile(r"(INSTAGRAM_LOGIN_APP_SECRET=)\S+", re.I),
    re.compile(r"(X-Amz-Signature=)[^&\s\"']+", re.I),
]


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        redacted = msg
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(r"\1***", redacted)
        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True


def setup_logging(level: str = "INFO", also_file: bool = True) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    stream.addFilter(RedactingFilter())
    root.addHandler(stream)
    if also_file:
        file_handler = logging.FileHandler(log_path(), encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(RedactingFilter())
        root.addHandler(file_handler)
    # Avoid per-request INFO spam from httpx during status polling
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def redact_text(value: str, extra: Iterable[str] | None = None) -> str:
    out = value
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(r"\1***", out)
    if extra:
        for secret in extra:
            if secret:
                out = out.replace(secret, "***")
    return out
