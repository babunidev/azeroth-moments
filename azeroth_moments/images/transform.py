"""Prepare Instagram derivative assets from a source screenshot."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from azeroth_moments.config import Config
from azeroth_moments.images.canvas import choose_single_target, fit_on_canvas
from azeroth_moments.images.carousel import plan_carousel_windows, render_carousel_panels
from azeroth_moments.images.classify import ImageMetrics, classify_shape
from azeroth_moments.images.decide import InstagramDecision, decide_instagram_mode
from azeroth_moments.images.meta_validate import validate_carousel_files, validate_jpeg_file
from azeroth_moments.paths import derivatives_dir

log = logging.getLogger(__name__)


@dataclass
class PreparedInstagramAssets:
    mode: str
    paths: list[Path]
    metrics: ImageMetrics
    decision: InstagramDecision
    panel_count: int
    panel_output_dims: list[tuple[int, int]] = field(default_factory=list)
    padding_applied: bool = False
    fallback_used: bool = False
    validation_ok: bool = False
    decision_summary: str = ""
    errors: list[str] = field(default_factory=list)
    media_kind: str = "image"


def _save_jpeg(image: Image.Image, path: Path, quality: int = 90) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="JPEG", quality=quality, optimize=True)


def prepare_instagram_assets(
    source_path: Path,
    config: Config,
    *,
    allow_fallback: bool = True,
) -> PreparedInstagramAssets:
    with Image.open(source_path) as im:
        im.load()
        rgb = im.convert("RGB")
        width, height = rgb.size
        metrics = classify_shape(width, height)
        decision = decide_instagram_mode(metrics, config)
        work_dir = derivatives_dir() / f"{source_path.stem}_{uuid.uuid4().hex[:10]}"
        work_dir.mkdir(parents=True, exist_ok=True)

        fallback_used = False
        padding_applied = False
        paths: list[Path] = []
        mode = decision.mode
        summary_parts = [decision.reason]

        try:
            if mode == "carousel":
                try:
                    plan = plan_carousel_windows(
                        width,
                        height,
                        min_panels=config.instagram_carousel_min_panels,
                        max_panels=config.instagram_carousel_max_panels,
                        default_panels=config.instagram_carousel_default_panels,
                        overlap_percent=config.instagram_carousel_overlap_percent,
                    )
                    panels, padded_flags = render_carousel_panels(
                        rgb,
                        plan,
                        padding_style=config.instagram_padding_style,
                    )
                    padding_applied = any(padded_flags)
                    for idx, panel in enumerate(panels):
                        out = work_dir / f"panel_{idx:02d}.jpg"
                        _save_jpeg(panel, out)
                        paths.append(out)
                    summary_parts.append(
                        f"carousel panels={plan.panel_count} covers={plan.covers_full_width}"
                    )
                    validation = validate_carousel_files(paths)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Carousel preparation failed for %s: %s", source_path, exc)
                    if allow_fallback and config.instagram_single_image_fallback:
                        mode = "single_padded"
                        fallback_used = True
                        paths = []
                        summary_parts.append(f"carousel prep failed; fallback ({exc})")
                    else:
                        return PreparedInstagramAssets(
                            mode="carousel",
                            paths=[],
                            metrics=metrics,
                            decision=decision,
                            panel_count=0,
                            padding_applied=False,
                            fallback_used=False,
                            validation_ok=False,
                            decision_summary="; ".join(summary_parts),
                            errors=[str(exc)],
                        )

            if mode in {"single", "single_padded"}:
                target = choose_single_target(metrics.aspect_ratio)
                if mode == "single" and abs(metrics.aspect_ratio - (target[0] / target[1])) < 0.02:
                    fitted, padded = fit_on_canvas(
                        rgb, target, padding_style=config.instagram_padding_style
                    )
                else:
                    fitted, padded = fit_on_canvas(
                        rgb, target, padding_style=config.instagram_padding_style
                    )
                    mode = "single_padded" if padded else mode
                padding_applied = padding_applied or padded
                out = work_dir / "single.jpg"
                _save_jpeg(fitted, out)
                paths = [out]
                validation = validate_jpeg_file(out, expect_size=fitted.size)

            panel_dims = []
            for p in paths:
                with Image.open(p) as im2:
                    panel_dims.append(im2.size)

            result = PreparedInstagramAssets(
                mode=mode,
                paths=paths,
                metrics=metrics,
                decision=decision,
                panel_count=len(paths),
                panel_output_dims=panel_dims,
                padding_applied=padding_applied,
                fallback_used=fallback_used,
                validation_ok=validation.ok,
                decision_summary="; ".join(summary_parts),
                errors=list(validation.errors),
            )
            if not validation.ok:
                log.error(
                    "Instagram derivative validation failed for %s: %s",
                    source_path,
                    validation.errors,
                )
            else:
                log.info(
                    "Prepared Instagram assets for %s mode=%s panels=%s",
                    source_path.name,
                    mode,
                    len(paths),
                )
            return result
        finally:
            rgb.close()
