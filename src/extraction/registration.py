"""Choosing which quadrilateral to treat as the card.

``canonicalize_from_source`` maps a quadrilateral onto a fixed 1000x630 frame, so
**the detected quad alone defines the canonical scale**. Every field region is
expressed as a fraction of that frame. If the quad is not the card's real
outline, the whole canonical card is mis-scaled and every fixed region points at
the wrong ink - while Phase 1 still reports a document score near 1.0, because
the score says how confident the detector is, not whether it was right.

Measured on the official labelled set (``reports/task3_registration.md``), on
cards Phase 1 *accepted*:

    group              median glyph px   quad_w / img_w   quad_h / img_h
    correct                       33.0            0.934            0.910
    short read 1-13               49.5            0.938            0.786
    no digits read                79.0            0.937            0.583

    (restricted to images whose own aspect ratio is already ID-1)

The quad's width is right and its height collapses. Warping that short band to
the full 630 px magnifies the card vertically, which is why failing crops have
*larger* glyphs than correct ones (49-79 px against 33 px) and why the national
ID leaves its box at one or both ends. Text size is not the cause; registration
is. This is a coordinate problem, not a recognition problem.

The remedy here is deliberately narrow. Re-registering every card is destructive:
measured on 200 cards that are currently read exactly right, replacing the
detected quad with the full image unconditionally **breaks 131 of them**. So the
fallback is gated on the detector having demonstrably failed - the ID region did
not yield 14 digits at any quarter turn - which leaves every card that already
reads 14 digits untouched by construction.
"""
import numpy as np

#: ISO/IEC 7810 ID-1, the bank-card format an Egyptian national ID uses:
#: 85.60 x 53.98 mm.
ID1_ASPECT_RATIO = 85.60 / 53.98

#: Fractional tolerance on the ID-1 aspect ratio. Tuned on the data; see
#: ``reports/task4_pre_cropped_bypass.md``.
DEFAULT_ASPECT_TOLERANCE = 0.08

#: Provenance recorded on every record, so which geometry produced a reading is
#: never a guess.
GEOMETRY_DETECTOR = 'doccornernet'
GEOMETRY_PRE_CROPPED_BYPASS = 'pre_cropped_bypass'
GEOMETRY_FULL_IMAGE_FALLBACK = 'full_image_fallback'
GEOMETRY_SOURCES = (GEOMETRY_DETECTOR, GEOMETRY_PRE_CROPPED_BYPASS,
                    GEOMETRY_FULL_IMAGE_FALLBACK)


def aspect_ratio(size):
    """Landscape aspect ratio of a ``(width, height)`` pair.

    Orientation-independent: a portrait photograph of a landscape card has the
    same shape, so the longer side is always the numerator.
    """
    width, height = float(size[0]), float(size[1])
    if width <= 0 or height <= 0:
        raise ValueError(f'image size must be positive, got {size!r}')
    return max(width, height) / min(width, height)


def aspect_deviation(size, reference=ID1_ASPECT_RATIO):
    """Fractional distance between an image's aspect ratio and ID-1's."""
    return abs(aspect_ratio(size) - reference) / reference


def looks_pre_cropped(size, tolerance=DEFAULT_ASPECT_TOLERANCE,
                      reference=ID1_ASPECT_RATIO):
    """True when the image is already cropped to the card's own shape.

    A photograph framed this tightly has no background around the card, so a
    document-corner detector has no boundary to find. That is a detector/input
    mismatch, not a quality problem: such an image is the *easiest* possible
    input for the OCR stage.
    """
    return aspect_deviation(size, reference) <= tolerance


def full_image_quad(size):
    """The whole image as a TL, TR, BR, BL quadrilateral.

    The correct reading of an already-cropped photograph: the card's outline is
    the image's own border.
    """
    width, height = int(size[0]), int(size[1])
    if width < 2 or height < 2:
        raise ValueError(f'image size must be at least 2x2, got {size!r}')
    return np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
                    dtype=np.float64)


def quad_edge_lengths(corners):
    """Mean width and mean height of a TL, TR, BR, BL quadrilateral."""
    c = np.asarray(corners, dtype=np.float64)
    if c.shape != (4, 2):
        raise ValueError(f'corners must have shape (4, 2), got {c.shape}')
    side = lambda p, q: float(np.linalg.norm(c[p] - c[q]))
    width = (side(0, 1) + side(2, 3)) / 2.0
    height = (side(1, 2) + side(3, 0)) / 2.0
    return width, height


def quad_aspect_ratio(corners):
    """Aspect ratio implied by a quadrilateral's mean edge lengths."""
    width, height = quad_edge_lengths(corners)
    if width <= 0 or height <= 0:
        raise ValueError('quadrilateral has a zero-length side')
    return max(width, height) / min(width, height)


def aspect_corrected_quad(corners, size, reference=ID1_ASPECT_RATIO):
    """Grow a quad along its short axis until its aspect ratio matches ID-1.

    The card is a physical ID-1 object, so a quad whose aspect ratio differs from
    ID-1 cannot be its outline. The detector is treated as right about the long
    axis and wrong about the short one, which is what the measurements show. The
    result is clamped to the image, because a card cannot extend beyond the
    photograph, and the quad is only ever grown - never cropped further in.

    Kept for diagnosis and for the record. It recovers far fewer cards than the
    full-image fallback (19 against 109 of 241), so it is not the shipped remedy.
    """
    c = np.asarray(corners, dtype=np.float64).copy()
    width, height = quad_edge_lengths(c)
    target_height = width / reference
    if target_height <= height:
        return c
    axis = ((c[2] + c[3]) / 2.0) - ((c[0] + c[1]) / 2.0)
    length = float(np.linalg.norm(axis))
    if length == 0:
        return c
    step = (target_height - height) / 2.0 * (axis / length)
    c[0] -= step
    c[1] -= step
    c[2] += step
    c[3] += step
    c[:, 0] = np.clip(c[:, 0], 0, int(size[0]) - 1)
    c[:, 1] = np.clip(c[:, 1], 0, int(size[1]) - 1)
    return c
