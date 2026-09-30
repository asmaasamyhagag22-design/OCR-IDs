"""Why a field failed, measured on the crop rather than guessed from the text.

Task-7 diagnosis. Every field that ends ``needs_review`` or ``unreadable`` gets
one observed cause, drawn from a closed list, and the measurement that produced
it. The list decides what preprocessing is worth adding next, so a cause that
was inferred from the OCR string rather than from pixels would be worse than
no cause at all.

Nothing here runs inside the extraction path. It reads the same crop the OCR
engine was given and reports on it; it never changes what was read.
"""

from dataclasses import dataclass

import cv2
import numpy as np

GLARE = "glare over the field"
ROTATED = "field rotated"
TOO_SMALL = "text too small"
CROP_CUT = "crop cut the field"
LOW_CONTRAST = "low contrast / faded"
PLAUSIBLE_UNVERIFIABLE = "OCR read a plausible but unverifiable value"

CAUSES = (GLARE, ROTATED, TOO_SMALL, CROP_CUT, LOW_CONTRAST, PLAUSIBLE_UNVERIFIABLE)

#: Fraction of the crop that must be blown out before glare is named the cause.
GLARE_FRACTION = 0.20
#: Ink within this many pixels of the crop edge counts as touching it.
BORDER_PIXELS = 2
#: Share of ink on the border above which the box is judged to have cut the field.
BORDER_INK_SHARE = 0.12
#: Degrees of skew above which the field is judged rotated inside its box.
SKEW_DEGREES = 5.0
#: Printed-line height, in canonical-card pixels, below which text is judged too
#: small.
#: Tesseract wants roughly 30px of x-height; the pipeline upscales 3x, so a
#: canonical glyph under 10px cannot reach that even after upscaling.
MIN_GLYPH_HEIGHT = 10.0
#: Separation between ink and paper, in grey levels, below which the print is
#: judged faded.
MIN_CONTRAST = 28.0
#: Share of the crop height the detected text band may occupy before the ink is
#: judged to be diffuse background rather than a printed line.
MAX_BAND_SHARE = 0.75


@dataclass(frozen=True)
class CropDiagnosis:
    """One measured verdict about one crop."""

    cause: str
    evidence: str
    measurements: dict

    def to_dict(self):
        return {"cause": self.cause, "evidence": self.evidence, "measurements": dict(self.measurements)}


def _grayscale(crop):
    array = np.asarray(crop)
    if array.ndim == 3:
        return cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
    return array


def _text_band(ink):
    """Rows the printed line actually occupies, as ``(top, bottom)`` or None.

    A median over connected components is not usable here: Arabic script joins
    letters into one wide component and scatters dots into tiny ones, and a
    busy security background adds speckles. The row profile of the ink is
    stable against all three, because a printed line is the one place where
    many columns have ink on the same rows.
    """
    profile = ink.sum(axis=1).astype(np.float32)
    if profile.max() <= 0:
        return None
    threshold = profile.max() * 0.20
    runs, start = [], None
    for row, value in enumerate(profile):
        if value > threshold and start is None:
            start = row
        elif value <= threshold and start is not None:
            runs.append((start, row))
            start = None
    if start is not None:
        runs.append((start, len(profile)))
    if not runs:
        return None
    return max(runs, key=lambda r: r[1] - r[0])


def measure(crop):
    """Measure one crop. Returns a plain dict; no interpretation."""
    gray = _grayscale(crop)
    height, width = gray.shape[:2]
    total = float(height * width) or 1.0

    glare_fraction = float((gray >= 250).sum()) / total

    # Ink is separated from paper by Otsu on a contrast-equalised copy, the same
    # separation the OCR path uses, so the measurement describes what the engine
    # actually saw.
    equalised = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    threshold, binary = cv2.threshold(equalised, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ink = binary > 0
    ink_fraction = float(ink.sum()) / total

    band = _text_band(ink)
    glyph_height = float(band[1] - band[0]) if band else 0.0

    # Contrast is measured inside the printed line only. Measuring it over the
    # whole crop lets one very dark object - the eagle emblem beside the birth
    # date, a barcode edge - report healthy contrast for a line that is in fact
    # invisible.
    contrast = 0.0
    if band:
        window = gray[band[0] : band[1], :]
        window_ink = ink[band[0] : band[1], :]
        if window_ink.any() and (~window_ink).any():
            contrast = float(window[~window_ink].mean() - window[window_ink].mean())

    border = np.zeros_like(ink)
    border[:BORDER_PIXELS, :] = True
    border[-BORDER_PIXELS:, :] = True
    border[:, :BORDER_PIXELS] = True
    border[:, -BORDER_PIXELS:] = True
    border_ink_share = float((ink & border).sum()) / float(ink.sum() or 1)

    skew = 0.0
    points = cv2.findNonZero(ink.astype(np.uint8))
    if points is not None and len(points) >= 5:
        angle = cv2.minAreaRect(points)[2]
        skew = float(angle - 90 if angle > 45 else angle)

    return {
        "crop_width_px": int(width),
        "crop_height_px": int(height),
        "text_band_px": list(band) if band else None,
        "glyph_height_px": round(glyph_height, 1),
        "ink_fraction": round(ink_fraction, 4),
        "ink_paper_contrast_grey_levels": round(contrast, 1),
        "glare_fraction": round(glare_fraction, 4),
        "border_ink_share": round(border_ink_share, 4),
        "skew_degrees": round(skew, 1),
        "otsu_threshold": float(threshold),
    }


def classify(crop, value=None, state=None):
    """Name the single most likely observed cause for this crop.

    Ordered most-specific first. A cause is only named when its own measurement
    crosses its own threshold; the fallback is the honest one, that OCR
    produced something that simply could not be confirmed.
    """
    m = measure(crop)

    if m["ink_fraction"] < 0.005:
        return CropDiagnosis(
            LOW_CONTRAST,
            f"almost no ink separates from the paper (ink fraction "
            f"{m['ink_fraction']:.4f}, ink/paper separation "
            f"{m['ink_paper_contrast_grey_levels']} grey levels)",
            m,
        )

    band_share = (m["glyph_height_px"] / m["crop_height_px"]) if m["crop_height_px"] else 0.0
    if band_share > MAX_BAND_SHARE:
        return CropDiagnosis(
            LOW_CONTRAST,
            f"no printed line separates from the background: the ink spreads over "
            f"{band_share * 100:.0f}% of the crop height instead of forming a line",
            m,
        )

    if m["glare_fraction"] >= GLARE_FRACTION:
        return CropDiagnosis(GLARE, f"{m['glare_fraction'] * 100:.0f}% of the crop is blown out to near-white", m)

    if m["border_ink_share"] >= BORDER_INK_SHARE:
        return CropDiagnosis(
            CROP_CUT,
            f"{m['border_ink_share'] * 100:.0f}% of the ink touches the box edge, "
            "so the printed field runs past the configured region",
            m,
        )

    if abs(m["skew_degrees"]) >= SKEW_DEGREES:
        return CropDiagnosis(
            ROTATED,
            f"the ink's principal axis is {m['skew_degrees']:.1f} degrees off horizontal inside an upright box",
            m,
        )

    if 0 < m["glyph_height_px"] < MIN_GLYPH_HEIGHT:
        return CropDiagnosis(
            TOO_SMALL,
            f"the printed line is only {m['glyph_height_px']:.1f}px tall on the canonical card "
            f"(~{m['glyph_height_px'] * 3:.0f}px after the 3x upscale, against the "
            "~30px Tesseract wants)",
            m,
        )

    if m["ink_paper_contrast_grey_levels"] < MIN_CONTRAST:
        return CropDiagnosis(
            LOW_CONTRAST, f"ink and paper are only {m['ink_paper_contrast_grey_levels']} grey levels apart", m
        )

    return CropDiagnosis(
        PLAUSIBLE_UNVERIFIABLE,
        (
            f"the crop is legible by measurement (line height {m['glyph_height_px']:.0f}px, "
            f"ink/paper separation {m['ink_paper_contrast_grey_levels']} grey levels, "
            f"skew {m['skew_degrees']:.1f} degrees) and OCR returned "
            + (f"{value!r}" if value else "nothing")
            + "; nothing on the card can confirm it"
        ),
        m,
    )
