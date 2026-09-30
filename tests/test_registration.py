"""Registration: which quadrilateral is treated as the card.

The canonical frame is defined entirely by that quadrilateral, so these are the
coordinates every fixed field region depends on. No real ID image is used.
"""
import math

import numpy as np
import pytest

from src.extraction.canonicalize import canonicalize_from_source
from src.extraction.registration import (DEFAULT_ASPECT_TOLERANCE, GEOMETRY_DETECTOR,
                                         GEOMETRY_FULL_IMAGE_FALLBACK,
                                         GEOMETRY_PRE_CROPPED_BYPASS, GEOMETRY_SOURCES,
                                         ID1_ASPECT_RATIO, aspect_corrected_quad,
                                         aspect_deviation, aspect_ratio, full_image_quad,
                                         looks_pre_cropped, quad_aspect_ratio,
                                         quad_edge_lengths)

#: The canonical card's own shape, which the region config is expressed against.
CANONICAL = (1000, 630)


# --------------------------------------------------------------------------
# the physical constant
# --------------------------------------------------------------------------

def test_the_aspect_ratio_is_the_iso_id1_card():
    """85.60 x 53.98 mm. A wrong constant here mis-registers every card."""
    assert ID1_ASPECT_RATIO == pytest.approx(85.60 / 53.98)
    assert ID1_ASPECT_RATIO == pytest.approx(1.5858, abs=0.0005)


def test_the_canonical_frame_matches_the_physical_card():
    """1000x630 is meant to be an ID-1 card; if it drifts, the ROIs drift."""
    assert aspect_deviation(CANONICAL) < 0.01


# --------------------------------------------------------------------------
# aspect ratio is orientation-independent
# --------------------------------------------------------------------------

@pytest.mark.parametrize('size', [(640, 403), (403, 640)])
def test_a_portrait_photograph_of_a_landscape_card_has_the_same_shape(size):
    """Rotating the file must not change whether it looks pre-cropped."""
    assert aspect_ratio(size) == pytest.approx(aspect_ratio(size[::-1]))
    assert looks_pre_cropped(size)


@pytest.mark.parametrize('size,expected', [
    ((1000, 630), True),        # the canonical card itself
    ((856, 540), True),         # ID-1 at 10 px/mm
    ((4032, 3024), False),      # 4:3 phone photograph
    ((1920, 1080), False),      # 16:9
    ((1000, 1000), False),      # square
])
def test_pre_cropped_detection_separates_card_shaped_from_camera_shaped(size, expected):
    assert looks_pre_cropped(size) is expected


def test_the_common_phone_aspect_ratios_are_outside_the_tolerance():
    """The bypass must not fire on an ordinary photograph of a card on a desk."""
    for size in ((4032, 3024), (1920, 1080)):
        assert aspect_deviation(size) > DEFAULT_ASPECT_TOLERANCE


def test_a_zero_or_negative_size_is_rejected_not_guessed():
    for bad in ((0, 100), (100, 0), (-5, 10)):
        with pytest.raises(ValueError):
            aspect_ratio(bad)


# --------------------------------------------------------------------------
# the full-image quad
# --------------------------------------------------------------------------

def test_the_full_image_quad_is_the_image_border_in_tl_tr_br_bl_order():
    quad = full_image_quad((100, 50))
    assert quad.tolist() == [[0, 0], [99, 0], [99, 49], [0, 49]]


def test_the_full_image_quad_is_accepted_by_the_canonicalizer():
    """It must survive validate_quadrilateral, or the fallback cannot be used."""
    image = np.full((50, 100, 3), 200, dtype=np.uint8)
    canonical = canonicalize_from_source(image, full_image_quad((100, 50)), 0,
                                         width=CANONICAL[0], height=CANONICAL[1])
    assert canonical.shape == (CANONICAL[1], CANONICAL[0], 3)


def test_a_degenerate_image_size_is_rejected():
    with pytest.raises(ValueError):
        full_image_quad((1, 1))


# --------------------------------------------------------------------------
# quad measurement
# --------------------------------------------------------------------------

def test_quad_edges_are_measured_as_the_mean_of_opposite_sides():
    quad = [[0, 0], [100, 0], [100, 40], [0, 40]]
    width, height = quad_edge_lengths(quad)
    assert width == pytest.approx(100)
    assert height == pytest.approx(40)
    assert quad_aspect_ratio(quad) == pytest.approx(2.5)


def test_a_wrongly_shaped_corner_array_is_rejected():
    with pytest.raises(ValueError):
        quad_edge_lengths([[0, 0], [1, 1]])


# --------------------------------------------------------------------------
# the aspect-corrected quad: grows the short axis, never crops
# --------------------------------------------------------------------------

def test_a_vertically_collapsed_quad_is_grown_to_the_card_aspect():
    """The measured failure mode: right width, collapsed height."""
    size = (1000, 800)
    short = [[0, 300], [900, 300], [900, 500], [0, 500]]      # aspect 4.5, far too wide
    fixed = aspect_corrected_quad(short, size)
    assert quad_aspect_ratio(fixed) == pytest.approx(ID1_ASPECT_RATIO, rel=0.02)
    # grown about the same centre, so the card's middle does not move
    before = np.mean(np.asarray(short, dtype=float), axis=0)
    after = np.mean(fixed, axis=0)
    assert after == pytest.approx(before, abs=1e-6)


def test_a_quad_that_already_matches_the_card_aspect_is_left_alone():
    """Within a sub-pixel: a quad already at the card's shape must not be moved.

    793/500 is a hair under ID-1, so the correction grows the height by 0.04 px.
    The contract is that a correct quad is left alone to well under one pixel,
    not that the arithmetic is exact.
    """
    quad = np.asarray([[0, 0], [793, 0], [793, 500], [0, 500]], dtype=float)
    assert quad_aspect_ratio(quad) == pytest.approx(ID1_ASPECT_RATIO, rel=0.01)
    moved = np.abs(aspect_corrected_quad(quad, (1000, 800)) - quad).max()
    assert moved < 0.1, f'a correctly shaped quad moved by {moved:.3f} px'


def test_correction_never_shrinks_a_quad_that_is_too_tall():
    """Only ever grows. Cropping further in is how the number gets lost."""
    tall = [[0, 0], [400, 0], [400, 700], [0, 700]]           # taller than ID-1
    fixed = aspect_corrected_quad(tall, (1000, 800))
    assert quad_edge_lengths(fixed)[1] >= quad_edge_lengths(tall)[1]


def test_correction_is_clamped_to_the_image():
    """A card cannot extend beyond the photograph that contains it."""
    size = (1000, 300)
    short = [[0, 100], [950, 100], [950, 200], [0, 200]]
    fixed = aspect_corrected_quad(short, size)
    assert fixed[:, 0].min() >= 0 and fixed[:, 0].max() <= size[0] - 1
    assert fixed[:, 1].min() >= 0 and fixed[:, 1].max() <= size[1] - 1


# --------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------

def test_every_geometry_source_is_named_and_distinct():
    """A reading's geometry must always be identifiable after the fact."""
    assert len(set(GEOMETRY_SOURCES)) == len(GEOMETRY_SOURCES)
    assert GEOMETRY_DETECTOR in GEOMETRY_SOURCES
    assert GEOMETRY_PRE_CROPPED_BYPASS in GEOMETRY_SOURCES
    assert GEOMETRY_FULL_IMAGE_FALLBACK in GEOMETRY_SOURCES


# --------------------------------------------------------------------------
# why registration matters at all: the scale is the quad
# --------------------------------------------------------------------------

def test_a_smaller_quad_magnifies_the_card_so_fixed_regions_move_off_target():
    """The mechanism behind the whole task, asserted rather than asserted in prose.

    A mark placed at 80% of the card's width lands at 80% of the canonical frame
    when the quad is the card, and somewhere else entirely when the quad is not.
    """
    image = np.full((200, 400, 3), 255, dtype=np.uint8)
    image[90:110, 310:330] = 0                      # a mark near the right edge

    def mark_centre_x(canonical):
        dark = np.argwhere(canonical.min(axis=2) < 128)
        return dark[:, 1].mean() / canonical.shape[1]

    whole = canonicalize_from_source(image, full_image_quad((400, 200)), 0,
                                    width=CANONICAL[0], height=CANONICAL[1])
    # a quad covering only the left 80% of the image: the same mark now sits
    # further right in the canonical frame, or leaves it altogether
    narrow = canonicalize_from_source(image, [[0, 0], [319, 0], [319, 199], [0, 199]], 0,
                                     width=CANONICAL[0], height=CANONICAL[1])
    assert mark_centre_x(whole) == pytest.approx(0.8, abs=0.02)
    assert mark_centre_x(narrow) > 0.95, 'a too-small quad pushes fixed regions off target'
