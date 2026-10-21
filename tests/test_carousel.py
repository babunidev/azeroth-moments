from PIL import Image

from azeroth_moments.images.carousel import (
    compute_panel_count,
    plan_carousel_windows,
    render_carousel_panels,
)


def test_panel_count_respects_bounds():
    n = compute_panel_count(
        1920, 1080, min_panels=2, max_panels=4, default_panels=3
    )
    assert 2 <= n <= 4


def test_windows_cover_full_width():
    plan = plan_carousel_windows(
        2400,
        1000,
        min_panels=2,
        max_panels=4,
        default_panels=3,
        overlap_percent=5,
    )
    assert plan.windows[0].left == 0
    assert plan.windows[-1].right == 2400
    assert plan.covers_full_width


def test_render_panels_consistent_4_5():
    img = Image.new("RGB", (1920, 1080), color=(40, 80, 120))
    # Draw distinct vertical bands so panels differ
    for x in range(1920):
        for y in range(1080):
            if x % 200 < 100:
                img.putpixel((x, y), (200, 50, 50))
    plan = plan_carousel_windows(
        1920, 1080, min_panels=2, max_panels=4, default_panels=3, overlap_percent=5
    )
    panels, flags = render_carousel_panels(img, plan)
    assert len(panels) == plan.panel_count
    sizes = {p.size for p in panels}
    assert sizes == {(1080, 1350)}
    # Overlap should not make every panel identical
    assert len({p.tobytes() for p in panels}) >= 2
