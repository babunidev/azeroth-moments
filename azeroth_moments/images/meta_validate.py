"""Validate Instagram derivatives against Meta content publishing constraints."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

# Documented / practical Instagram feed image constraints
MIN_ASPECT = 0.8  # 4:5
MAX_ASPECT = 1.91
MAX_BYTES = 8 * 1024 * 1024
MIN_WIDTH = 320
MIN_HEIGHT = 320


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: list[str]


def validate_jpeg_file(path: Path, *, expect_size: tuple[int, int] | None = None) -> ValidationResult:
    errors: list[str] = []
    if not path.is_file():
        return ValidationResult(False, [f"Missing file: {path}"])
    size = path.stat().st_size
    if size <= 0:
        errors.append("Empty file")
    if size > MAX_BYTES:
        errors.append(f"File too large: {size} > {MAX_BYTES}")
    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            if im.format != "JPEG":
                errors.append(f"Format must be JPEG, got {im.format}")
            w, h = im.size
            if w < MIN_WIDTH or h < MIN_HEIGHT:
                errors.append(f"Dimensions too small: {w}x{h}")
            aspect = w / h
            if aspect < MIN_ASPECT - 0.01 or aspect > MAX_ASPECT + 0.01:
                errors.append(f"Aspect {aspect:.4f} outside [{MIN_ASPECT}, {MAX_ASPECT}]")
            if expect_size and (w, h) != expect_size:
                errors.append(f"Expected size {expect_size}, got {(w, h)}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Invalid image: {exc}")
    return ValidationResult(ok=not errors, errors=errors)


def validate_carousel_files(paths: list[Path]) -> ValidationResult:
    if not paths:
        return ValidationResult(False, ["No carousel panels"])
    errors: list[str] = []
    sizes: list[tuple[int, int]] = []
    for path in paths:
        result = validate_jpeg_file(path)
        errors.extend(f"{path.name}: {e}" for e in result.errors)
        try:
            with Image.open(path) as im:
                sizes.append(im.size)
        except Exception:
            continue
    if sizes and len(set(sizes)) != 1:
        errors.append(f"Inconsistent carousel dimensions: {sizes}")
    return ValidationResult(ok=not errors, errors=errors)
