"""Quarter-turn source-corner correspondence and canonical warp tests."""
import cv2
import numpy as np
import pytest

from src.extraction.canonicalize import (
    CANONICAL_HEIGHT,
    CANONICAL_WIDTH,
    canonicalize_from_source,
    cyclic_corner_correspondence,
    restore_corner_correspondence,
)


SOURCE_CORNERS = np.float32([[30, 20], [369, 20], [369, 229], [30, 229]])
CORNER_COLOURS = np.uint8([
    [240, 20, 20],    # TL
    [20, 220, 20],    # TR
    [20, 20, 240],    # BR
    [230, 210, 20],   # BL
])


def _labelled_source():
    image = np.full((250, 400, 3), 245, dtype=np.uint8)
    for point, colour in zip(SOURCE_CORNERS.astype(int), CORNER_COLOURS):
        cv2.circle(image, tuple(point), 14, tuple(int(v) for v in colour), -1)
    return image


@pytest.mark.parametrize('turns,expected_indices', [
    (0, [0, 1, 2, 3]),
    (1, [1, 2, 3, 0]),
    (2, [2, 3, 0, 1]),
    (3, [3, 0, 1, 2]),
])
def test_all_four_cyclic_corner_mappings(turns, expected_indices):
    mapped = cyclic_corner_correspondence(SOURCE_CORNERS, turns)
    np.testing.assert_array_equal(mapped, SOURCE_CORNERS[expected_indices])


@pytest.mark.parametrize('turns', range(4))
def test_inverse_mapping_restores_original_correspondence(turns):
    mapped = cyclic_corner_correspondence(SOURCE_CORNERS, turns)
    restored = restore_corner_correspondence(mapped, turns)
    np.testing.assert_array_equal(restored, SOURCE_CORNERS)


@pytest.mark.parametrize('turns', range(4))
def test_canonical_output_size_and_known_corner_labels(turns):
    output = canonicalize_from_source(_labelled_source(), SOURCE_CORNERS, turns)
    assert output.shape == (CANONICAL_HEIGHT, CANONICAL_WIDTH, 3)

    # Sample slightly inside each destination corner to avoid border rounding.
    sample_points = [(20, 15), (CANONICAL_WIDTH - 21, 15),
                     (CANONICAL_WIDTH - 21, CANONICAL_HEIGHT - 16),
                     (20, CANONICAL_HEIGHT - 16)]
    expected = np.roll(CORNER_COLOURS, -turns, axis=0)
    for (x, y), colour in zip(sample_points, expected):
        assert np.linalg.norm(output[y, x].astype(float) - colour.astype(float)) < 35


@pytest.mark.parametrize('turns', range(4))
def test_quarter_turn_warp_is_not_mirrored(turns):
    output = canonicalize_from_source(_labelled_source(), SOURCE_CORNERS, turns)
    samples = np.array([
        output[22, 35],
        output[22, CANONICAL_WIDTH - 36],
        output[CANONICAL_HEIGHT - 23, CANONICAL_WIDTH - 36],
        output[CANONICAL_HEIGHT - 23, 35],
    ])
    expected = np.roll(CORNER_COLOURS, -turns, axis=0)
    reversed_order = expected[[0, 3, 2, 1]]
    direct_error = np.abs(samples.astype(int) - expected.astype(int)).sum()
    mirrored_error = np.abs(samples.astype(int) - reversed_order.astype(int)).sum()
    assert direct_error < mirrored_error


def test_mapping_does_not_mutate_input_points():
    points = SOURCE_CORNERS.copy()
    cyclic_corner_correspondence(points, 3)
    np.testing.assert_array_equal(points, SOURCE_CORNERS)


@pytest.mark.parametrize('invalid', [-1, 4, 1.5, True, '1'])
def test_invalid_quarter_turns_are_rejected(invalid):
    expected = TypeError if invalid in (1.5, True, '1') else ValueError
    with pytest.raises(expected):
        cyclic_corner_correspondence(SOURCE_CORNERS, invalid)
