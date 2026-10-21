from pathlib import Path

from PIL import Image

from azeroth_moments.images.meta_validate import validate_carousel_files, validate_jpeg_file


def test_validate_jpeg_ok(tmp_path: Path):
    path = tmp_path / "ok.jpg"
    Image.new("RGB", (1080, 1350), color=(10, 20, 30)).save(path, format="JPEG")
    result = validate_jpeg_file(path)
    assert result.ok


def test_validate_rejects_png(tmp_path: Path):
    path = tmp_path / "bad.png"
    Image.new("RGB", (1080, 1350), color=(10, 20, 30)).save(path, format="PNG")
    # reopen as jpeg extension but wrong format — save as png with jpg name
    path = tmp_path / "bad.jpg"
    Image.new("RGB", (1080, 1350), color=(10, 20, 30)).save(path, format="PNG")
    result = validate_jpeg_file(path)
    assert not result.ok


def test_carousel_consistent_dims(tmp_path: Path):
    paths = []
    for i in range(3):
        p = tmp_path / f"p{i}.jpg"
        Image.new("RGB", (1080, 1350), color=(i * 40, 20, 30)).save(p, format="JPEG")
        paths.append(p)
    assert validate_carousel_files(paths).ok
