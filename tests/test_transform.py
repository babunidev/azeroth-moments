from pathlib import Path

from PIL import Image

from azeroth_moments.images.transform import prepare_instagram_assets
from tests.conftest import make_config


def test_landscape_carousel(tmp_path: Path, monkeypatch):
    import azeroth_moments.images.transform as transform_mod

    deriv = tmp_path / "deriv"
    deriv.mkdir()
    monkeypatch.setattr(transform_mod, "derivatives_dir", lambda: deriv)
    src = tmp_path / "land.png"
    Image.new("RGB", (1920, 1080), color=(12, 34, 56)).save(src)
    assets = prepare_instagram_assets(src, make_config(tmp_path, dry_run=True))
    assert assets.mode == "carousel"
    assert assets.panel_count >= 2
    assert assets.validation_ok
    assert all(p.suffix == ".jpg" for p in assets.paths)


def test_square_single(tmp_path: Path, monkeypatch):
    import azeroth_moments.images.transform as transform_mod

    deriv = tmp_path / "deriv"
    deriv.mkdir()
    monkeypatch.setattr(transform_mod, "derivatives_dir", lambda: deriv)
    src = tmp_path / "sq.png"
    Image.new("RGB", (1000, 1000), color=(12, 34, 56)).save(src)
    assets = prepare_instagram_assets(src, make_config(tmp_path, dry_run=True))
    assert assets.mode in {"single", "single_padded"}
    assert assets.panel_count == 1
    assert assets.validation_ok
