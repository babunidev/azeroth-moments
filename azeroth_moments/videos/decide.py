"""Choose Instagram video output mode from shape + config."""

from __future__ import annotations

from dataclasses import dataclass

from azeroth_moments.config import Config
from azeroth_moments.images.classify import ImageMetrics


@dataclass(frozen=True)
class InstagramVideoDecision:
    mode: str  # single | preview_carousel
    reason: str


def decide_instagram_video_mode(metrics: ImageMetrics, config: Config) -> InstagramVideoDecision:
    landscape_mode = config.instagram_video_landscape_mode.lower()
    shape = metrics.shape

    if shape in {"landscape", "extra_wide"}:
        if landscape_mode == "single":
            return InstagramVideoDecision("single", f"{shape}: forced single")
        if landscape_mode == "adaptive":
            if metrics.aspect_ratio >= 1.35:
                return InstagramVideoDecision(
                    "preview_carousel", f"{shape}: adaptive preview_carousel"
                )
            return InstagramVideoDecision("single", f"{shape}: adaptive single")
        return InstagramVideoDecision(
            "preview_carousel", f"{shape}: default preview_carousel"
        )

    if shape in {"portrait", "square", "extra_tall", "irregular"}:
        return InstagramVideoDecision("single", f"{shape}: single video")

    return InstagramVideoDecision("single", "fallback single")
