"""Shape classification for freeform screenshots."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImageMetrics:
    width: int
    height: int
    aspect_ratio: float
    shape: str


def classify_shape(width: int, height: int) -> ImageMetrics:
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive")
    aspect = width / height
    if 0.95 <= aspect <= 1.05:
        shape = "square"
    elif aspect < 0.5:
        shape = "extra_tall"
    elif aspect > 2.4:
        shape = "extra_wide"
    elif aspect < 0.9:
        shape = "portrait"
    elif aspect > 1.15:
        shape = "landscape"
    else:
        shape = "irregular"
    return ImageMetrics(width=width, height=height, aspect_ratio=aspect, shape=shape)
