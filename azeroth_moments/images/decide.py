"""Choose Instagram output mode from classified shape and config."""

from __future__ import annotations

from dataclasses import dataclass

from azeroth_moments.config import Config
from azeroth_moments.images.classify import ImageMetrics


@dataclass(frozen=True)
class InstagramDecision:
    mode: str  # single | carousel | single_padded
    reason: str


def decide_instagram_mode(metrics: ImageMetrics, config: Config) -> InstagramDecision:
    shape = metrics.shape
    landscape_mode = config.instagram_landscape_mode.lower()

    if shape in {"landscape", "extra_wide"}:
        if landscape_mode == "single":
            return InstagramDecision("single_padded", f"{shape}: forced single")
        if landscape_mode == "adaptive":
            # Use carousel when wide enough to benefit from multi-panel
            if metrics.aspect_ratio >= 1.35:
                return InstagramDecision("carousel", f"{shape}: adaptive carousel")
            return InstagramDecision("single_padded", f"{shape}: adaptive single")
        return InstagramDecision("carousel", f"{shape}: default carousel")

    if shape == "square":
        return InstagramDecision("single", "square single")

    if shape == "portrait":
        # Within IG portrait bounds (~4:5 = 0.8)
        if 0.8 <= metrics.aspect_ratio <= 1.0:
            return InstagramDecision("single", "portrait in bounds")
        return InstagramDecision("single_padded", "portrait out of bounds")

    if shape == "extra_tall":
        return InstagramDecision("single_padded", "extra_tall padded")

    # irregular
    if metrics.aspect_ratio > 1.35 and landscape_mode != "single":
        return InstagramDecision("carousel", "irregular landscape-suitable carousel")
    return InstagramDecision("single_padded", "irregular padded")
