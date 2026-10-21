"""Build Discord/Instagram captions, optionally derived from the filename."""

from __future__ import annotations

import re
from pathlib import Path

DEFAULT_TAGLINE = "Azeroth, through my eyes."
DEFAULT_HASHTAGS = (
    "#BabuniGaming #WorldOfWarcraft #WoWForever #Azeroth #AzerothMoments"
)

_WS_RE = re.compile(r"\s+")


def custom_text_from_filename(name: str) -> str | None:
    """Return custom caption text when the basename starts with ``?``.

    ``?arriving+in+Redridge.png`` → ``arriving in Redridge``.
    Filenames without a leading ``?`` return ``None`` (use default caption).
    """
    stem = Path(name).stem
    if not stem.startswith("?"):
        return None
    raw = stem[1:].replace("+", " ")
    text = _WS_RE.sub(" ", raw).strip()
    if not text:
        return None
    return text


def build_caption(
    path: Path | str,
    *,
    tagline: str = DEFAULT_TAGLINE,
    hashtags: str = DEFAULT_HASHTAGS,
    base_caption: str | None = None,
) -> str:
    """Assemble the publish caption for *path*.

    If *base_caption* is provided (e.g. a full ``DEFAULT_CAPTION`` from config),
    it is split into tagline + hashtag block. Otherwise *tagline* / *hashtags*
    are used. When the filename starts with ``?``, a dashed middle line is
    inserted between tagline and hashtags.
    """
    if base_caption is not None and base_caption.strip():
        tagline_part, hashtag_part = _split_base_caption(base_caption)
    else:
        tagline_part = tagline.strip() or DEFAULT_TAGLINE
        hashtag_part = hashtags.strip() or DEFAULT_HASHTAGS

    custom = custom_text_from_filename(Path(path).name)
    if custom is None:
        return f"{tagline_part}\n\n{hashtag_part}"
    return f"{tagline_part}\n\n-{custom}-\n\n{hashtag_part}"


def _split_base_caption(base: str) -> tuple[str, str]:
    """Split a stored caption into tagline and trailing hashtag block."""
    text = base.replace("\r\n", "\n").strip()
    lines = text.split("\n")
    hash_idx: int | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            hash_idx = i
            break
    if hash_idx is None:
        return text, DEFAULT_HASHTAGS
    tagline = "\n".join(lines[:hash_idx]).strip() or DEFAULT_TAGLINE
    hashtags = "\n".join(lines[hash_idx:]).strip() or DEFAULT_HASHTAGS
    return tagline, hashtags
