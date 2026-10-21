from azeroth_moments.images.classify import classify_shape


def test_square():
    m = classify_shape(1000, 1000)
    assert m.shape == "square"


def test_landscape():
    m = classify_shape(1920, 1080)
    assert m.shape == "landscape"


def test_extra_wide():
    m = classify_shape(3000, 1000)
    assert m.shape == "extra_wide"


def test_portrait():
    m = classify_shape(800, 1200)
    assert m.shape == "portrait"


def test_extra_tall():
    m = classify_shape(400, 1200)
    assert m.shape == "extra_tall"
