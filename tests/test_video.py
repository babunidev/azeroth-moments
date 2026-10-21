from pathlib import Path

import pytest

from azeroth_moments.images.classify import classify_shape
from azeroth_moments.ingest import media_kind_for
from azeroth_moments.videos.decide import decide_instagram_video_mode
from azeroth_moments.videos.ffmpeg_tools import ffmpeg_available
from azeroth_moments.videos.transform import prepare_instagram_video_assets
from tests.conftest import make_config


def test_media_kind_detection():
    assert media_kind_for(Path("a.PNG")) == "image"
    assert media_kind_for(Path("b.mp4")) == "video"
    assert media_kind_for(Path("c.mov")) == "video"
    assert media_kind_for(Path("d.mkv")) == "video"
    assert media_kind_for(Path("e.txt")) is None


def test_landscape_video_chooses_preview_carousel():
    metrics = classify_shape(1920, 1080)
    decision = decide_instagram_video_mode(metrics, make_config())
    assert decision.mode == "preview_carousel"


def test_portrait_video_chooses_single():
    metrics = classify_shape(1080, 1920)
    decision = decide_instagram_video_mode(metrics, make_config())
    assert decision.mode == "single"


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")
def test_prepare_landscape_preview_carousel(tmp_path: Path, monkeypatch):
    import azeroth_moments.videos.transform as transform_mod

    deriv = tmp_path / "deriv"
    deriv.mkdir()
    monkeypatch.setattr(transform_mod, "derivatives_dir", lambda: deriv)

    src = tmp_path / "land.mp4"
    # Generate a short synthetic landscape clip with ffmpeg
    from azeroth_moments.videos.ffmpeg_tools import run_ffmpeg

    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=1280x720:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(src),
        ],
        timeout=60,
    )
    cfg = make_config(tmp_path, instagram_video_max_duration_seconds=1.0)
    assets = prepare_instagram_video_assets(src, cfg)
    assert assets.validation_ok, assets.errors
    assert assets.mode == "preview_carousel"
    assert assets.panel_labels == ["preview", "left", "middle", "right"]
    assert len(assets.paths) == 4
    assert all(p.suffix == ".mp4" and p.stat().st_size > 1000 for p in assets.paths)
    # Original untouched
    assert src.is_file()
