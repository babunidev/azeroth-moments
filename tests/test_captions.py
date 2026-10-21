from __future__ import annotations

from pathlib import Path

from azeroth_moments.captions import (
    DEFAULT_HASHTAGS,
    DEFAULT_TAGLINE,
    build_caption,
    custom_text_from_filename,
)
from azeroth_moments.config import DEFAULT_CAPTION, Config
from tests.conftest import make_config


def test_default_hashtag_is_azeroth_moments():
    assert "#AzerothMoments" in DEFAULT_HASHTAGS
    assert "#VirtualPhotography" not in DEFAULT_HASHTAGS
    assert "#VirtualPhotography" not in DEFAULT_CAPTION
    assert DEFAULT_CAPTION == f"{DEFAULT_TAGLINE}\n\n{DEFAULT_HASHTAGS}"


def test_no_question_mark_returns_none():
    assert custom_text_from_filename("Screenshot_20261012_191502.png") is None
    assert custom_text_from_filename("Replay 2026-10-12 04-03-09.mp4") is None
    assert custom_text_from_filename("arriving+in+Redridge.png") is None


def test_question_mark_plus_to_spaces():
    assert (
        custom_text_from_filename("?arriving+in+Redridge.png")
        == "arriving in Redridge"
    )
    assert (
        custom_text_from_filename("?Arriving+in+Redridge.mp4")
        == "Arriving in Redridge"
    )


def test_edge_cases():
    assert custom_text_from_filename("?.png") is None
    assert custom_text_from_filename("?+++.png") is None
    assert custom_text_from_filename("?hello+++world.png") == "hello world"
    assert custom_text_from_filename("?  spaced+out  .png") == "spaced out"


def test_build_caption_default():
    caption = build_caption("Screenshot_x.png")
    assert caption == f"{DEFAULT_TAGLINE}\n\n{DEFAULT_HASHTAGS}"
    assert "-Screenshot" not in caption


def test_build_caption_custom_with_dashes():
    caption = build_caption(Path("/tmp/?arriving+in+Redridge.png"))
    assert caption == (
        "Azeroth, through my eyes.\n\n"
        "-arriving in Redridge-\n\n"
        "#BabuniGaming #WorldOfWarcraft #WoWForever #Azeroth #AzerothMoments"
    )


def test_build_caption_video():
    caption = build_caption("?sunset+over+Stormwind.mp4")
    assert "-sunset over Stormwind-" in caption
    assert caption.endswith(DEFAULT_HASHTAGS)


def test_config_caption_for_path(tmp_path: Path):
    cfg: Config = make_config(tmp_path)
    plain = cfg.caption_for("instagram", tmp_path / "normal.png")
    assert "#AzerothMoments" in plain
    assert plain.count("\n\n") == 1

    custom = cfg.caption_for("discord", tmp_path / "?hello+Azeroth.png")
    assert "-hello Azeroth-" in custom
    assert "#AzerothMoments" in custom


def test_platform_override_still_gets_custom_line(tmp_path: Path):
    cfg = make_config(
        tmp_path,
        discord_caption=(
            "Custom tagline.\n\n#BabuniGaming #WorldOfWarcraft #WoWForever "
            "#Azeroth #AzerothMoments"
        ),
    )
    caption = cfg.caption_for("discord", "?from+override.png")
    assert caption.startswith("Custom tagline.")
    assert "-from override-" in caption
