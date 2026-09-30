"""The one pipeline behind the service and the CLI: image bytes in, a reading out.

`Recognizer.recognize(image: bytes)` is the only entry point. It takes the
upload's bytes and nothing else: the typed ID cannot reach the models, by the
signature (a TypeError on anything that is not bytes) and by the tests.

The steps (configs/phase4_service_rule_v1.json `pipeline`):

1. decode by magic bytes (src/extraction/image_decode.py, as it stands); a video
   returns a video outcome, anything else undecodable raises UndecodableImage;
2. the frozen first pass (fixed14_bcfinal_shadow.compare_image);
3. F1v, only when the first pass returns document_not_found: the configuration-0
   second pass run ungated, then the validity gate applied to its result. The
   ungated cascade stops at the first step that yields a band, which is where the
   gated form stops too; the gated reading is kept aside for the CLI only;
4. BC-final reads the band (inside compare_image, hash-checked at load);
5. F8, only when the card is admitted and the reading is structurally invalid;
6. F9, only on the safety rule's trigger (region_search.triggered): no reading, an
   invalid reading, or a margin below the threshold. A valid reading at or above the
   threshold never reaches it.

Nothing here knows the typed ID or makes a decision.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, cast

import numpy as np

from src.extraction import admission_second_pass as sp
from src.extraction import fixed14_demo_adapter as adapter
from src.extraction import fixed14_v3_shadow as v3_shadow
from src.extraction import image_decode as idc
from src.extraction import validity_reread as vr

from . import region_search as rs

#: F1v's configuration: configuration 0 of the declared grid (s_min 0.30, pad 0.10, no full-image
#: quad), run WITHOUT its validity gate. The recognizer applies the gate itself
#: (`_first_pass_with_f1v`) so that it can keep the refused reading for the CLI's `gated_reading`.
#: The ungated cascade stops at the first step that yields a band, which is exactly where the gated
#: form stops, so the two agree (stage-1 amendment 4 D2).
F1V_UNGATED_CONFIG = sp.GRID[0]
#: F8 walks configuration 0's cascade after the other quarter turns (stage-1 amendment 3 C7).
F8_CONFIG = sp.GRID[0]
#: F9's acceptance margin for a card that had no valid reading (configs/phase4_final_rule_v1.json
#: amendment 2), chosen on the development pool only (artifacts/phase4_final/f9/score.json). It
#: equals the frozen routing threshold (versions.margin_threshold()) by selection, not by
#: definition: the two are separate settings and must be changed separately.
F9_ACCEPT_MARGIN = 0.966875

#: The labels written to `Recognition.fixes`, the CLI's `fixes_triggered` column and the logs.
FIX_F1V_RECOVERED = "F1v:recovered"
FIX_F1V_GATED = "F1v:gated"
FIX_F1V_NOT_RECOVERED = "F1v:not_recovered"
FIX_F8_RECOVERED = "F8:recovered"
FIX_F8_NOT_RECOVERED = "F8:not_recovered"
FIX_F9_ACCEPTED = "F9:accepted"
FIX_F9_NOT_ACCEPTED = "F9:not_accepted"

REASON_UPLOAD_IS_VIDEO = idc.REASON_UPLOAD_IS_VIDEO
REASON_CARD_NOT_FOUND = "CARD_NOT_FOUND"
REASON_NUMBER_LINE_NOT_FOUND = "NUMBER_LINE_NOT_FOUND"
#: Frozen admission statuses and the reason code each maps to (D-035).
ADMISSION_REASONS = {
    adapter.DOCUMENT_NOT_FOUND: REASON_CARD_NOT_FOUND,
    adapter.INVALID_CORNERS: REASON_CARD_NOT_FOUND,
    adapter.PERSPECTIVE_CORRECTION_FAILURE: REASON_CARD_NOT_FOUND,
    adapter.ORIENTATION_FAILURE: REASON_NUMBER_LINE_NOT_FOUND,
    adapter.BROAD_BAND_EXTRACTION_FAILURE: REASON_NUMBER_LINE_NOT_FOUND,
}

OUTCOME_READ = "read"
OUTCOME_NOT_ADMITTED = "not_admitted"
OUTCOME_VIDEO = "video"


class UndecodableImage(ValueError):
    """The bytes are not an image the decoder can read (the service's HTTP 400)."""

    def __init__(self, status: str, fmt: str):
        super().__init__(status)
        self.status = status
        self.format = fmt


class PipelineError(RuntimeError):
    """The frozen pipeline failed in a way that is not an admission failure (HTTP 500)."""


class ImageTooLarge(ValueError):
    """The decoded image would exceed the pixel cap (the service's HTTP 413, D-051)."""


#: The service's cap on decoded pixels (D-051). The largest permitted development upload
#: is 52.1 MP (a 50 MP phone photo; a PDF page of 20.8 x 27.8 in at 300 DPI).
SERVICE_MAX_MEGAPIXELS = 60.0
RASTER_FORMATS = frozenset({"jpeg", "png", "webp", "gif", "bmp", "tiff", "heif", "avif"})


def decoded_megapixels(payload: bytes, fmt: str) -> float | None:
    """The pixel count decoding would produce, from the header only (nothing is decoded).

    PDF: page 1's size at 300 DPI, as image_decode renders it. Raster: the header's
    width x height. None when the header cannot be read: the decoder then decides.
    """
    import io

    try:
        if fmt == "pdf":
            import pypdfium2 as pdfium

            document = pdfium.PdfDocument(io.BytesIO(payload))
            try:
                if len(document) == 0:
                    return None
                width, height = document[0].get_size()
            finally:
                document.close()
            scale = idc.PDF_RASTER_DPI / 72.0
            return float(width * scale * height * scale / 1e6)
        if fmt in RASTER_FORMATS:
            from PIL import Image

            if fmt in ("heif", "avif"):
                idc._register_heif()
            with Image.open(io.BytesIO(payload)) as opened:  # lazy: reads the header only
                width, height = opened.size
            return float(width * height / 1e6)
    except Exception as exc:  # noqa: BLE001
        from PIL import Image

        if isinstance(exc, Image.DecompressionBombError):
            return float("inf")
        return None
    return None


@dataclass(frozen=True)
class FirstPass:
    """What the frozen pipeline alone produced. For measurement; never returned by the service."""

    admitted: bool
    reading: str | None
    margin: float | None
    admission_status: str


@dataclass(frozen=True)
class Recognition:
    """What the pipeline read from one upload. Never holds the typed ID, never a decision.

    - `outcome`: OUTCOME_READ, OUTCOME_NOT_ADMITTED (no card or no number line) or OUTCOME_VIDEO.
    - `admitted`, `reading`, `margin`: whether a number line was read, BC-final's 14 digits, and
      BC-final's weakest-position margin (the decider compares it with the threshold).
    - `models_agree`: whether the v2 reader read the same 14 digits on the same band (descriptive).
    - `reason_code`: why a card was not admitted (ADMISSION_REASONS, or the video reason), else None.
    - `fixes`: the recovery steps that ran, in order (the FIX_* labels).
    - `first_pass`: what the frozen pipeline alone produced, for measurement.
    - `gated_reading`, `gated_margin`: a reading F1v's validity gate refused; the card stays not
      admitted and only the CLI writes it.
    """

    outcome: str
    decode_format: str
    admitted: bool
    reading: str | None
    margin: float | None
    models_agree: bool
    reason_code: str | None
    fixes: tuple[str, ...]
    first_pass: FirstPass | None
    admission_status: str | None = None
    geometry_source: str | None = None
    gated_reading: str | None = field(default=None, repr=False)
    gated_margin: float | None = None
    processing_ms: float = 0.0


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)


def _v2_digits(runtime: Any, band: Any) -> str:
    """The v2 reader's digits on a band, or "" if it fails: `models_agree` is descriptive only, so a
    v2 failure must never fail the request."""
    try:
        return v3_shadow._digits(runtime.predict(np.asarray(band)))
    except Exception:  # noqa: BLE001 - descriptive only
        return ""


def _is_admitted(result: Any) -> bool:
    return result.band is not None and bool(getattr(result, "bc_final_national_id", ""))


class Recognizer:
    """Loads the frozen bundle once; `recognize` reads one upload."""

    def __init__(
        self, runtime: Any = None, max_megapixels: float | None = SERVICE_MAX_MEGAPIXELS, f9: bool = True
    ) -> None:
        if runtime is None:
            from .runtime import ServiceRuntime

            runtime = ServiceRuntime()
        self.runtime = runtime
        #: None: no cap (the competition CLI's default, D-051)
        self.max_megapixels = max_megapixels
        #: F9 on (the shipped pipeline); off reproduces pipeline phase4-service-f1v-f8-v1 exactly
        self.f9 = f9

    @property
    def threshold(self) -> float:
        """The frozen routing threshold, which F9's trigger uses. The decider reads its own copy
        (ServiceState.threshold); both come from versions.margin_threshold()."""
        from .versions import margin_threshold

        return margin_threshold()

    def load(self) -> None:
        """Load the frozen models and run their sealed self-checks (once per process)."""
        self.runtime.ensure_shadow_loaded()

    def recognize(self, image: bytes) -> Recognition:
        """Read one upload's bytes.

        Raises ImageTooLarge (over the pixel cap), UndecodableImage (not an image the decoder
        reads) or PipelineError (a frozen-pipeline failure). A video is an outcome, not an error.
        """
        if not isinstance(image, (bytes, bytearray, memoryview)):
            raise TypeError("Recognizer.recognize takes the image bytes only")
        started = time.perf_counter()
        payload = bytes(image)
        if self.max_megapixels is not None:
            fmt = idc.sniff_format(payload[: idc.HEAD_BYTES])
            megapixels = decoded_megapixels(payload, fmt)
            if megapixels is not None and megapixels > self.max_megapixels:
                raise ImageTooLarge(fmt)
        decoded = idc.decode_bytes(payload)
        if decoded.status == idc.VIDEO_NOT_AN_IMAGE:
            return Recognition(
                outcome=OUTCOME_VIDEO,
                decode_format=decoded.format,
                admitted=False,
                reading=None,
                margin=None,
                models_agree=False,
                reason_code=REASON_UPLOAD_IS_VIDEO,
                fixes=(),
                first_pass=None,
                processing_ms=_elapsed_ms(started),
            )
        if not decoded.ok:
            raise UndecodableImage(decoded.status, decoded.format)
        return self._read(cast("np.ndarray[Any, Any]", decoded.rgb), decoded.format, started)

    def _read(self, rgb: np.ndarray[Any, Any], fmt: str, started: float) -> Recognition:
        """The pipeline on a decoded image: the first pass with F1v, then F8 and F9 as they apply."""
        self.runtime.ensure_shadow_loaded()
        fixes: list[str] = []
        result, first, first_pass_result, gated_reading, gated_margin = self._first_pass_with_f1v(rgb, fixes)
        if result is None or not _is_admitted(result):
            return self._not_admitted(
                rgb, fmt, started, result, first, first_pass_result, gated_reading, gated_margin, fixes
            )
        return self._admitted(rgb, fmt, started, result, first, first_pass_result, fixes)

    def _first_pass_with_f1v(
        self, rgb: np.ndarray[Any, Any], fixes: list[str]
    ) -> tuple[Any, FirstPass, Any, str | None, float | None]:
        """The frozen first pass, and F1v only when it returns document_not_found.

        Returns (result, first-pass summary, first_pass_result, gated reading, gated margin).
        `result` is None when the card stays not admitted after F1v. `first_pass_result` is the
        first pass's own result, whose quad F9 re-turns; None when F1v ran (there is no first-pass
        band to re-turn).
        """
        gated_reading = gated_margin = None
        result, info = sp.read_with_second_pass(rgb, self.runtime, F1V_UNGATED_CONFIG)
        first_pass_result = None if info["triggered"] else result
        if info["triggered"]:
            # the first pass returned document_not_found; read_with_second_pass returned
            # either that very result (nothing recovered) or the recovered one
            first = FirstPass(False, None, None, sp.TRIGGER_STATUS)
            if info.get("recovered"):
                if sp.structurally_valid(result.bc_final_national_id):
                    fixes.append(FIX_F1V_RECOVERED)
                else:
                    # the F1v gate: an impossible reading is certainly wrong; the card stays not admitted
                    fixes.append(FIX_F1V_GATED)
                    gated_reading, gated_margin = result.bc_final_national_id, result.bc_final_margin
                    result = None
            else:
                fixes.append(FIX_F1V_NOT_RECOVERED)
                result = None
        else:
            first = FirstPass(
                _is_admitted(result),
                result.bc_final_national_id or None,
                result.bc_final_margin,
                result.admission_status,
            )
        return result, first, first_pass_result, gated_reading, gated_margin

    def _not_admitted(
        self,
        rgb: np.ndarray[Any, Any],
        fmt: str,
        started: float,
        result: Any,
        first: FirstPass,
        first_pass_result: Any,
        gated_reading: str | None,
        gated_margin: float | None,
        fixes: list[str],
    ) -> Recognition:
        """No card or no number line: F9 may still find one; otherwise the admission reason."""
        # result None: F1v ran and left the card not admitted (document_not_found)
        status = sp.TRIGGER_STATUS if result is None else result.admission_status
        if result is not None and status not in ADMISSION_REASONS:
            raise PipelineError(status or "unknown")
        found = self._f9(rgb, first_pass_result=first_pass_result, admitted=False, reading=None, fixes=fixes)
        if found is not None:
            return self._read_result(found, fmt, fixes, first, status, started)
        return Recognition(
            outcome=OUTCOME_NOT_ADMITTED,
            decode_format=fmt,
            admitted=False,
            reading=None,
            margin=None,
            models_agree=False,
            reason_code=ADMISSION_REASONS[status],
            fixes=tuple(fixes),
            first_pass=first,
            admission_status=status,
            geometry_source=None if result is None else result.geometry_source,
            gated_reading=gated_reading,
            gated_margin=gated_margin,
            processing_ms=_elapsed_ms(started),
        )

    def _admitted(
        self,
        rgb: np.ndarray[Any, Any],
        fmt: str,
        started: float,
        result: Any,
        first: FirstPass,
        first_pass_result: Any,
        fixes: list[str],
    ) -> Recognition:
        """An admitted card: F8 on a structurally impossible reading, then F9 on its trigger."""
        if not sp.structurally_valid(result.bc_final_national_id):
            reread, f8 = vr.reread_if_invalid(rgb, self.runtime, F8_CONFIG, first=result)
            fixes.append(FIX_F8_RECOVERED if f8.get("recovered") else FIX_F8_NOT_RECOVERED)
            result = reread
        found = self._f9(
            rgb,
            first_pass_result=first_pass_result,
            admitted=True,
            reading=result.bc_final_national_id,
            fixes=fixes,
            margin=float(result.bc_final_margin),
        )
        if found is not None:
            return self._read_result(found, fmt, fixes, first, getattr(result, "admission_status", None), started)
        reading = result.bc_final_national_id
        v2 = getattr(result, "v2_national_id", None)
        if v2 is None:  # an F8 re-read at another turn: v2 reads the same band
            v2 = _v2_digits(self.runtime, result.band)
        return Recognition(
            outcome=OUTCOME_READ,
            decode_format=fmt,
            admitted=True,
            reading=reading,
            margin=float(result.bc_final_margin),
            models_agree=v2 == reading,
            reason_code=None,
            fixes=tuple(fixes),
            first_pass=first,
            admission_status=getattr(result, "admission_status", None),
            geometry_source=result.geometry_source,
            processing_ms=_elapsed_ms(started),
        )

    def _f9(
        self,
        rgb: np.ndarray[Any, Any],
        *,
        first_pass_result: Any,
        admitted: bool,
        reading: str | None,
        fixes: list[str],
        margin: float | None = None,
    ) -> rs.Candidate | None:
        """F9 (configs/phase4_final_rule_v1.json amendment 2), only on the safety rule's trigger.

        Returns the accepted candidate, or None when F9 is off, did not trigger, or accepted nothing.
        """
        if not self.f9 or not rs.triggered(admitted, reading, margin, self.threshold):
            return None
        chosen, _info = rs.search(
            rgb,
            self.runtime,
            first_result=first_pass_result,
            admitted=admitted,
            reading=reading,
            threshold=self.threshold,
            accept_margin=F9_ACCEPT_MARGIN,
        )
        fixes.append(FIX_F9_ACCEPTED if chosen is not None else FIX_F9_NOT_ACCEPTED)
        return chosen

    def _read_result(
        self,
        found: rs.Candidate,
        fmt: str,
        fixes: list[str],
        first: FirstPass | None,
        status: str | None,
        started: float,
    ) -> Recognition:
        """The Recognition for a reading F9 accepted."""
        v2 = _v2_digits(self.runtime, found.band)
        return Recognition(
            outcome=OUTCOME_READ,
            decode_format=fmt,
            admitted=True,
            reading=found.reading,
            margin=float(found.margin),
            models_agree=v2 == found.reading,
            reason_code=None,
            fixes=tuple(fixes),
            first_pass=first,
            admission_status=status,
            geometry_source=found.geometry_source,
            processing_ms=_elapsed_ms(started),
        )
