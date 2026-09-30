"""Decode an uploaded card photo by what its bytes are, never by its file name.

Phones save HEIF/HEIC (iPhone) or AVIF photos, and an upload can still end in
`.jpg`. One audit card's original was an ISO BMFF file named `Nid_Front.jpg`
(D-031). The frozen adapter (fixed14_demo_adapter.decode_image) accepts JPEG and
PNG only and stays as it is. The service and the CLI decode through this module
(Recognizer.recognize), then hand the RGB array to the unchanged pipeline, which
accepts arrays.

- The format comes from magic bytes. For ISO BMFF (an `ftyp` box at byte 4) the
  brands decide: HEIF/HEIC, AVIF, video, or unknown.
- HEIF, HEIC and AVIF decode through pillow-heif, pinned at 0.18.0 in
  requirements.txt and pyproject.toml (1.x needs Pillow 11, and Pillow 10.4.0 is
  part of the model contract).
- A real video returns `video_not_an_image`: a clear status, not a decode error. The
  recognizer turns it into UNREADABLE with reason code UPLOAD_IS_VIDEO;
  `service_outcome` below states the same routing and is used by the tests only.
- PDF (amendment 7): page 1 rendered at 300 DPI (pypdfium2 5.13.0, pinned),
  exactly as ingest rendered every PDF card BC-final has seen.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

DECODED = "decoded"
VIDEO_NOT_AN_IMAGE = "video_not_an_image"
UNSUPPORTED_FORMAT = "unsupported_format"
CORRUPT_IMAGE = "corrupt_image"
EMPTY_FILE = "empty_file"
TOO_LARGE = "file_too_large"

#: Coupled to the service's OCR_MAX_UPLOAD_MB (default 25): above this, decode_bytes returns
#: file_too_large, which the service answers as 400 (not 413) and the CLI as UNDECODABLE. A
#: deployment that raises OCR_MAX_UPLOAD_MB above 25 hits this first (a known issue, left for an
#: owner decision in Stage 7 because changing it changes a response).
MAX_BYTES = 25 * 1024 * 1024
#: Enough for the ftyp box's major brand, version and several compatible brands.
HEAD_BYTES = 64

HEIF_BRANDS = frozenset(
    {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"hevm", b"hevs", b"mif1", b"msf1", b"mif2"}
)
AVIF_BRANDS = frozenset({b"avif", b"avis"})
VIDEO_BRANDS = frozenset(
    {
        b"isom",
        b"iso2",
        b"iso3",
        b"iso4",
        b"iso5",
        b"iso6",
        b"mp41",
        b"mp42",
        b"avc1",
        b"qt  ",
        b"M4V ",
        b"M4VP",
        b"3gp4",
        b"3gp5",
        b"3gp6",
        b"3g2a",
        b"dash",
        b"mmp4",
        b"f4v ",
        b"MSNV",
        b"XAVC",
    }
)
DECODABLE = frozenset({"jpeg", "png", "webp", "gif", "bmp", "tiff", "heif", "avif"})
PDF_RASTER_DPI = 300


def ftyp_brands(head: bytes) -> dict | None:
    """The ftyp box of an ISO BMFF file: major brand, minor version, compatible brands.

    Reads only what it is given (the first bytes). None when there is no ftyp box.
    """
    if len(head) < 16 or head[4:8] != b"ftyp":
        return None
    size = int.from_bytes(head[:4], "big")
    end = min(size if size >= 16 else len(head), len(head))
    compatible = [head[i : i + 4] for i in range(16, end - 3, 4)]
    return {"major": head[8:12], "minor_version": int.from_bytes(head[12:16], "big"), "compatible": compatible}


def classify_brands(brands: dict) -> str:
    """heif, avif, video or iso_bmff_unknown. The major brand decides first.

    Order matters: an AVIF lists mif1 among its compatible brands, and an iPhone
    HEIC lists mif1 as well, so AVIF is tested before the generic HEIF brands.
    """
    for candidates in ([brands["major"]], brands["compatible"]):
        found = set(candidates)
        if found & AVIF_BRANDS:
            return "avif"
        if found & HEIF_BRANDS:
            return "heif"
        if found & VIDEO_BRANDS:
            return "video"
    return "iso_bmff_unknown"


def sniff_format(head: bytes) -> str:
    """The container, from magic bytes only."""
    if not head:
        return "empty"
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[4:8] == b"ftyp":
        # known issue (Stage 7, owner decision pending): a payload of under 16 bytes with `ftyp` at
        # offset 4 makes ftyp_brands return None, and classify_brands then raises TypeError
        return classify_brands(ftyp_brands(head))
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:2] == b"BM":
        return "bmp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[:4] == b"%PDF":
        return "pdf"
    return "unknown"


def brand_text(brands: dict | None) -> dict | None:
    """The ftyp brands as text (latin-1), for reports; None stays None."""
    if brands is None:
        return None
    text = lambda b: b.decode("latin-1")  # noqa: E731
    return {
        "major": text(brands["major"]),
        "minor_version": brands["minor_version"],
        "compatible": [text(b) for b in brands["compatible"]],
    }


@dataclass
class DecodeResult:
    """A decode's status (DECODED or a failure status), the sniffed format, and the upright RGB array."""

    status: str
    format: str
    rgb: np.ndarray | None = field(default=None, repr=False)
    brands: dict | None = None
    detail: dict | None = None

    @property
    def ok(self) -> bool:
        """Whether `rgb` holds a decoded image."""
        return self.status == DECODED


def _register_heif() -> None:
    import pillow_heif

    pillow_heif.register_heif_opener()
    pillow_heif.register_avif_opener()


def decode_bytes(payload: bytes) -> DecodeResult:
    """Bytes to an upright RGB array (EXIF orientation applied), or a clear status."""
    from PIL import Image, ImageOps

    if not payload:
        return DecodeResult(EMPTY_FILE, "empty")
    if len(payload) > MAX_BYTES:
        return DecodeResult(TOO_LARGE, sniff_format(payload[:HEAD_BYTES]))
    fmt = sniff_format(payload[:HEAD_BYTES])
    brands = brand_text(ftyp_brands(payload[:HEAD_BYTES]))
    if fmt == "video":
        return DecodeResult(VIDEO_NOT_AN_IMAGE, fmt, brands=brands)
    if fmt == "pdf":
        return decode_pdf(payload)
    if fmt not in DECODABLE:
        return DecodeResult(UNSUPPORTED_FORMAT, fmt, brands=brands)
    if fmt in ("heif", "avif"):
        _register_heif()
    try:
        with Image.open(io.BytesIO(payload)) as opened:
            rgb = np.asarray(ImageOps.exif_transpose(opened).convert("RGB"), dtype=np.uint8)
    except Exception:  # noqa: BLE001 - any decoder failure is reported, not raised
        return DecodeResult(CORRUPT_IMAGE, fmt, brands=brands)
    if rgb.ndim != 3 or rgb.shape[2] != 3 or min(rgb.shape[:2]) < 2:
        return DecodeResult(CORRUPT_IMAGE, fmt, brands=brands)
    return DecodeResult(DECODED, fmt, np.ascontiguousarray(rgb), brands)


def decode_pdf(payload: bytes) -> DecodeResult:
    """Page 1 rendered at 300 DPI, exactly as ingest does (amendment 7).

    Train/serve consistency: every PDF card BC-final was trained, validated and
    audited on reached it as ingest's 300-DPI render of page 1
    (official_eval.transcode). Serving the same pixels keeps that contract; the
    native embedded raster has a different scale (amendment 6, superseded).
    """
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(io.BytesIO(payload))
    except Exception:  # noqa: BLE001
        return DecodeResult(CORRUPT_IMAGE, "pdf")
    try:
        pages = len(document)
        if pages == 0:
            return DecodeResult(CORRUPT_IMAGE, "pdf", detail={"pages": 0})
        bitmap = document[0].render(scale=PDF_RASTER_DPI / 72.0)
        rgb = np.asarray(bitmap.to_pil().convert("RGB"), dtype=np.uint8)
        return DecodeResult(
            DECODED,
            "pdf",
            np.ascontiguousarray(rgb),
            detail={"pages": pages, "page": 1, "method": f"rendered_{PDF_RASTER_DPI}dpi"},
        )
    except Exception:  # noqa: BLE001
        return DecodeResult(CORRUPT_IMAGE, "pdf")
    finally:
        document.close()


# --------------------------------------------------------------------------- routing an upload that is not an image

UNREADABLE = "UNREADABLE"
#: Reason codes the backend can act on (docs/release/ocr_api_contract_v1.md).
REASON_UPLOAD_IS_VIDEO = "UPLOAD_IS_VIDEO"


def service_outcome(result: DecodeResult) -> dict | None:
    """The service response for an upload that must not reach the pipeline, else None.

    A video saved under an image name (an MP4 called `Nid_Front.jpg`, D-031) is
    not a bad photograph: no retake of the same file can help. It routes to
    UNREADABLE with a reason code, so the backend can ask for a photo or a scan
    of the card instead. Other decode failures keep the contract's HTTP errors.
    """
    if result.status == VIDEO_NOT_AN_IMAGE:
        return {
            "decision": UNREADABLE,
            "reason_code": REASON_UPLOAD_IS_VIDEO,
            "suggested_validation": None,
            "reading": None,
            "admitted": False,
            "detail": "The upload is a video, not an image. Ask the applicant for a photo or a scan of the card.",
        }
    return None


def decode_file(path: str | Path) -> DecodeResult:
    """A file to an RGB array. The extension is never consulted."""
    from src.governance import guard

    guard.check_card_read(path)
    try:
        payload = Path(path).read_bytes()
    except OSError:
        return DecodeResult(CORRUPT_IMAGE, "unreadable")
    return decode_bytes(payload)
