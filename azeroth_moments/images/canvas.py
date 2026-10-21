"""Canvas fitting with blurred-background padding for Instagram."""

from __future__ import annotations

from PIL import Image, ImageFilter


TARGET_PORTRAIT = (1080, 1350)  # 4:5
TARGET_LANDSCAPE = (1080, 566)  # ~1.91:1
TARGET_SQUARE = (1080, 1080)


def fit_on_canvas(
    image: Image.Image,
    target_size: tuple[int, int],
    *,
    padding_style: str = "blurred_background",
) -> tuple[Image.Image, bool]:
    """Fit entire image into target without cropping; returns (image, padding_applied)."""
    image = image.convert("RGB")
    tw, th = target_size
    src_w, src_h = image.size
    scale = min(tw / src_w, th / src_h)
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)

    if new_w == tw and new_h == th:
        return resized, False

    if padding_style == "blurred_background":
        bg = image.resize((tw, th), Image.Resampling.LANCZOS).filter(
            ImageFilter.GaussianBlur(radius=24)
        )
    else:
        bg = Image.new("RGB", (tw, th), (20, 20, 24))

    canvas = bg.copy()
    offset = ((tw - new_w) // 2, (th - new_h) // 2)
    canvas.paste(resized, offset)
    return canvas, True


def choose_single_target(aspect: float) -> tuple[int, int]:
    """Choose a legal Instagram feed canvas for a single-image post."""
    # Instagram feed: 4:5 (0.8) to 1.91:1
    if aspect < 0.85:
        return TARGET_PORTRAIT
    if aspect > 1.7:
        return TARGET_LANDSCAPE
    if 0.95 <= aspect <= 1.05:
        return TARGET_SQUARE
    # Keep nearest legal by fitting into portrait or landscape
    if aspect < 1.0:
        return TARGET_PORTRAIT
    return TARGET_LANDSCAPE
