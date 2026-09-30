"""Canonical reading-orientation transforms for rectified ID cards.

Orientation selection is intentionally outside this module.  The caller
supplies an explicit number of counter-clockwise quarter turns.  The warp is
computed from the original image and detected source corners, avoiding a
rotate-and-resize pass over an already rectified crop.
"""
from __future__ import annotations

import cv2
import numpy as np

from src.geometry.quadrilateral import validate_quadrilateral

CANONICAL_WIDTH = 1000
CANONICAL_HEIGHT = 630


def _validate_quarter_turns(quarter_turns_ccw: int) -> int:
    if isinstance(quarter_turns_ccw, bool) or not isinstance(quarter_turns_ccw, (int, np.integer)):
        raise TypeError('quarter_turns_ccw must be an integer in {0, 1, 2, 3}')
    value = int(quarter_turns_ccw)
    if value not in (0, 1, 2, 3):
        raise ValueError('quarter_turns_ccw must be one of 0, 1, 2, or 3')
    return value


def cyclic_corner_correspondence(points, quarter_turns_ccw: int):
    """Return source corners corresponding to upright TL, TR, BR, BL.

    ``points`` must already be TL, TR, BR, BL in the source image.  A CCW
    quarter turn makes the old TR the new TL, hence the left roll.
    """
    turns = _validate_quarter_turns(quarter_turns_ccw)
    corners = np.asarray(points)
    if corners.shape != (4, 2):
        raise ValueError(f'points must have shape (4, 2), got {corners.shape}')
    if not np.issubdtype(corners.dtype, np.number) or not np.isfinite(corners).all():
        raise ValueError('points must contain finite numeric coordinates')
    return np.roll(corners.copy(), -turns, axis=0)


def restore_corner_correspondence(points, quarter_turns_ccw: int):
    """Undo :func:`cyclic_corner_correspondence`."""
    turns = _validate_quarter_turns(quarter_turns_ccw)
    corners = np.asarray(points)
    if corners.shape != (4, 2):
        raise ValueError(f'points must have shape (4, 2), got {corners.shape}')
    if not np.issubdtype(corners.dtype, np.number) or not np.isfinite(corners).all():
        raise ValueError('points must contain finite numeric coordinates')
    return np.roll(corners.copy(), turns, axis=0)


def canonicalize_from_source(
    rgb,
    source_corners,
    quarter_turns_ccw: int,
    width: int = CANONICAL_WIDTH,
    height: int = CANONICAL_HEIGHT,
    tolerance: float = 0.02,
    min_area_fraction: float = 0.02,
):
    """Re-warp an original image to an upright landscape card.

    The source polygon is validated in its original TL/TR/BR/BL order.  Only
    its corner-to-destination correspondence changes for the requested
    orientation; the points are never repaired by sorting.
    """
    turns = _validate_quarter_turns(quarter_turns_ccw)
    if width < 2 or height < 2:
        raise ValueError('Output dimensions must be at least 2')
    image = np.asarray(rgb)
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError('rgb must have shape (height, width, 3)')

    corners = np.asarray(source_corners, dtype=np.float32)
    reason = validate_quadrilateral(
        corners, image.shape[1], image.shape[0], tolerance, min_area_fraction
    )
    if reason:
        raise ValueError(reason)

    source = cyclic_corner_correspondence(corners, turns).astype(np.float32)
    target = np.float32([
        [0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]
    ])
    matrix = cv2.getPerspectiveTransform(source, target)
    return cv2.warpPerspective(image, matrix, (width, height), flags=cv2.INTER_LINEAR)
