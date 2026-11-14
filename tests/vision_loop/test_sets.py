from pathlib import Path

import numpy as np

from bubble.geom import Camera
from vision_loop.sets import build_sets


def cam(name, xyz, heading, pitch=0.0, image="refs/a.png"):
    return Camera(name, "fit", xyz, heading, pitch, 60, 32, 24, image)


def test_same_place_and_facing_share_a_set():
    cameras = (
        cam("a", (0, 0, 2), 10),
        cam("b", (3, 1, 2), 25),
    )
    sets = build_sets(cameras)
    assert len(sets) == 1
    assert set(sets[0]["cameras"]) == {"a", "b"}


def test_opposite_headings_split_even_at_the_same_place():
    cameras = (
        cam("north", (0, 0, 2), 0),
        cam("south", (1, 0, 2), 180),
    )
    sets = build_sets(cameras)
    assert len(sets) == 2
    assert {sets[0]["cameras"][0], sets[1]["cameras"][0]} == {"north", "south"}


def test_distant_cameras_split():
    cameras = (
        cam("here", (0, 0, 2), 0),
        cam("there", (500, 0, 2), 5),
    )
    sets = build_sets(cameras)
    assert len(sets) == 2


def test_downward_photo_does_not_join_an_oblique_set():
    cameras = (
        cam("rover", (0, 0, 2), 40, pitch=-20),
        cam("nadir", (0, 0, 30), 40, pitch=-90),
    )
    sets = build_sets(cameras)
    assert len(sets) == 2


def test_set_caps_at_four_images():
    cameras = tuple(cam(f"c{i}", (i * 0.2, 0, 2), 5) for i in range(6))
    sets = build_sets(cameras, max_images=4)
    sizes = sorted(len(item["cameras"]) for item in sets)
    assert sizes == [2, 4]
    assert sum(sizes) == 6


def test_fixture_fit_and_holdout_are_one_set():
    from bubble.bundle import load_bundle

    bundle = load_bundle(Path("contracts/fixtures/bubble_source"))
    sets = build_sets(bundle.cameras)
    names = [name for item in sets for name in item["cameras"]]
    assert "nadir" not in names
    assert len(sets) == 1
    assert set(sets[0]["cameras"]) == {"fit_0", "holdout_0"}
    assert sets[0]["focus_order"][0] in {"fit_0", "holdout_0"}
    # The two forwards are not opposite.
    assert len(sets[0]["focus_order"]) == 2
    assert np.isfinite(bundle.dem).all()
