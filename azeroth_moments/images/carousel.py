"""Geometry-driven landscape carousel panel generation."""

from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image

from azeroth_moments.images.canvas import TARGET_PORTRAIT, fit_on_canvas


@dataclass(frozen=True)
class PanelWindow:
    left: int
    top: int
    right: int
    bottom: int


@dataclass(frozen=True)
class CarouselPlan:
    panel_count: int
    windows: list[PanelWindow]
    overlap_percent: float
    covers_full_width: bool


def compute_panel_count(
    width: int,
    height: int,
    *,
    min_panels: int,
    max_panels: int,
    default_panels: int,
) -> int:
    """Choose panel count so portrait windows can cover the full width."""
    if width <= 0 or height <= 0:
        raise ValueError("Invalid dimensions")
    # Ideal window width for a 4:5 crop of full height
    ideal_window_w = height * 4 / 5
    if ideal_window_w <= 0:
        return min_panels
    # How many such windows needed to cover width with modest overlap
    needed = math.ceil(width / ideal_window_w)
    # Prefer default when image is a typical landscape
    aspect = width / height
    if 1.4 <= aspect <= 2.0:
        candidate = default_panels
    else:
        candidate = needed
    return max(min_panels, min(max_panels, candidate))


def plan_carousel_windows(
    width: int,
    height: int,
    *,
    min_panels: int,
    max_panels: int,
    default_panels: int,
    overlap_percent: float,
) -> CarouselPlan:
    n = compute_panel_count(
        width,
        height,
        min_panels=min_panels,
        max_panels=max_panels,
        default_panels=default_panels,
    )
    # Panel width as a fraction of source width, with overlap between adjacent panels
    overlap = max(0.0, min(overlap_percent, 49.0)) / 100.0
    if n == 1:
        windows = [PanelWindow(0, 0, width, height)]
        return CarouselPlan(1, windows, overlap_percent, True)

    # Effective stride: (1 - overlap) of panel width
    # n * panel_w - (n-1) * overlap * panel_w = width
    # panel_w * (n - (n-1)*overlap) = width
    denom = n - (n - 1) * overlap
    panel_w = width / denom
    windows: list[PanelWindow] = []
    for i in range(n):
        left = int(round(i * panel_w * (1 - overlap)))
        right = int(round(left + panel_w))
        if i == n - 1:
            # Ensure last panel reaches the right edge (full scene coverage)
            right = width
            left = max(0, min(left, width - 1))
        left = max(0, min(left, width - 1))
        right = max(left + 1, min(right, width))
        windows.append(PanelWindow(left, 0, right, height))

    # Ensure first starts at 0
    first = windows[0]
    windows[0] = PanelWindow(0, first.top, first.right, first.bottom)
    covers = windows[0].left == 0 and windows[-1].right == width
    return CarouselPlan(n, windows, overlap_percent, covers)


def render_carousel_panels(
    image: Image.Image,
    plan: CarouselPlan,
    *,
    padding_style: str = "blurred_background",
) -> tuple[list[Image.Image], list[bool]]:
    panels: list[Image.Image] = []
    padded_flags: list[bool] = []
    rgb = image.convert("RGB")
    for window in plan.windows:
        crop = rgb.crop((window.left, window.top, window.right, window.bottom))
        if crop.size[0] < 1 or crop.size[1] < 1:
            raise ValueError("Carousel produced an empty panel")
        fitted, padded = fit_on_canvas(crop, TARGET_PORTRAIT, padding_style=padding_style)
        panels.append(fitted)
        padded_flags.append(padded)
    return panels, padded_flags
