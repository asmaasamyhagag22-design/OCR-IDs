"""Constrained per-region OCR.

OCR runs on one cropped field region at a time, never on the whole card, and
every call is constrained to the character set the config declares for that
field. The constraint is applied twice: as a Tesseract whitelist, and again as a
hard post-filter. Tesseract's LSTM engine is documented to honour
``tessedit_char_whitelist`` only partially, so the post-filter is what actually
guarantees the contract. Anything it removes is recorded as a note, and the raw
engine output is always preserved.

Every read returns a :class:`RawOcrResult`. No function here returns a bare
string.
"""

import hashlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ARABIC_LETTERS = "".join(chr(c) for c in list(range(0x0621, 0x063B)) + list(range(0x0641, 0x064B)))
#: Tatweel and tashkeel are permitted in a *value* so it stays faithful to the
#: card. They are removed only in the comparison form, by text_norm.normalize_arabic.
ARABIC_MARKS = "ـ" + "".join(chr(c) for c in range(0x064B, 0x0653)) + "ٰ"
ARABIC_INDIC = "".join(chr(c) for c in range(0x0660, 0x066A))

LATIN_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

OFFICIAL_PADDLE_MODEL_SOURCES = {
    "arabic_PP-OCRv3_mobile_rec": "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/arabic_PP-OCRv3_mobile_rec_infer.tar",
    "arabic_PP-OCRv5_mobile_rec": "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/arabic_PP-OCRv5_mobile_rec_infer.tar",
    "latin_PP-OCRv3_mobile_rec": "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/latin_PP-OCRv3_mobile_rec_infer.tar",
}

CHARSETS = {
    "digits": "0123456789" + ARABIC_INDIC,
    "latin_serial": LATIN_UPPER + "0123456789",
    "date_digits": "0123456789" + ARABIC_INDIC + "/-. ",
    "arabic_text": ARABIC_LETTERS + ARABIC_MARKS + " ",
    "none": "",
}

#: The numerals printed on these cards are Arabic-Indic, which the `eng` model
#: cannot emit at all. Measured on the Phase 1 corpus, `ara` recovers roughly
#: 15 digits per card against 2-8 for `eng`.
#: The card serial is printed in Latin letters and Western digits, which the
#: `ara` model reads as Arabic letters or not at all. Measured on this corpus:
#: `eng` returns the full serial on four of five fronts; `ara` returns one or
#: two stray digits on all five.
LANG_BY_CONTENT = {"digits": "ara", "date_digits": "ara", "arabic_text": "ara", "latin_serial": "eng"}


def whitelist_for(content):
    """The charset actually sent to Tesseract, which is not always the full one.

    Measured on this corpus: a numeric whitelist improves digit recall, but an
    Arabic-letter whitelist makes Tesseract's LSTM engine return nothing at all
    on every card and every psm. So no whitelist is sent for Arabic text; the
    declared charset is still enforced afterwards, in pipeline._read_region.
    """
    return "" if content == "arabic_text" else CHARSETS.get(content, "")


@dataclass(frozen=True)
class RawOcrResult:
    """What the engine returned, before any field-level interpretation."""

    text: str
    confidence: float = None
    engine: str = "unknown"
    engine_version: str = "unknown"
    params: dict = field(default_factory=dict)
    word_confidences: tuple = ()
    notes: tuple = ()

    def to_dict(self):
        return {
            "text": self.text,
            "confidence": self.confidence,
            "engine": self.engine,
            "engine_version": self.engine_version,
            "params": dict(self.params),
            "word_confidences": list(self.word_confidences),
            "notes": list(self.notes),
        }


def apply_charset(text, charset):
    """Hard-enforce the declared charset. Returns ``(kept, notes)``."""
    if not charset:
        return text, ()
    allowed = set(charset)
    kept = "".join(c for c in text if c in allowed)
    removed = len(text) - len(kept)
    notes = (f"charset_filter_removed_{removed}_characters",) if removed else ()
    return kept.strip(), notes


class ScriptedEngine:
    """Deterministic test double. Maps a region name to a scripted read.

    Used by the test suite so the decoder, the cross-verification and the state
    machine can be exercised without Tesseract or any real ID image.
    """

    name = "scripted"
    version = "1"

    def __init__(self, responses=None, default=("", None)):
        self.responses = dict(responses or {})
        self.default = default
        self.calls = []

    def recognize(self, image, charset="", lang="eng", psm=7, region_name=""):
        self.calls.append({"region_name": region_name, "charset": charset, "lang": lang, "psm": psm})
        text, confidence = self.responses.get(region_name, self.default)
        return RawOcrResult(
            text=text,
            confidence=confidence,
            engine=self.name,
            engine_version=self.version,
            params={"lang": lang, "psm": psm, "charset_size": len(charset)},
            notes=("scripted_engine",),
        )


class TesseractEngine:
    """pytesseract-backed raw field-crop recognizer.

    Phase 2C deliberately performs no image preprocessing.  The crop received
    from the audited region configuration is passed to Tesseract unchanged.
    Optional constructor arguments are retained for API compatibility, but a
    non-raw setting is rejected so the baseline cannot silently drift.
    """

    name = "tesseract"

    def __init__(self, tesseract_cmd=None, tessdata_dir=None, oem=1, upscale=1.0, binarise=False, border_px=0):
        import pytesseract  # imported lazily so the package works without Tesseract

        self._pytesseract = pytesseract
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = str(tesseract_cmd)
        self.tessdata_dir = str(tessdata_dir) if tessdata_dir else None
        if self.tessdata_dir:
            # Not --tessdata-dir: pytesseract splits the config with
            # shlex.split(posix=False) on Windows, which keeps the quotes inside
            # the token and makes Tesseract look for '"C:\...\tessdata"/ara'.
            # The environment variable takes the path verbatim.
            os.environ["TESSDATA_PREFIX"] = self.tessdata_dir
        self.oem = int(oem)
        self.upscale = float(upscale)
        self.binarise = bool(binarise)
        self.border_px = int(border_px)
        if self.upscale != 1.0 or self.binarise or self.border_px:
            raise ValueError(
                "Phase 2C raw baseline forbids OCR preprocessing; use upscale=1.0, binarise=False, border_px=0"
            )
        self.version = str(pytesseract.get_tesseract_version())
        self.runtime_versions = {
            "tesseract": self.version,
            "pytesseract": getattr(pytesseract, "__version__", "unknown"),
        }

    def preprocess(self, image):
        """Return the audited field crop unchanged."""
        return np.asarray(image)

    def recognize(self, image, charset="", lang="eng", psm=7, region_name=""):
        prepared = self.preprocess(image)
        config = f"--oem {self.oem} --psm {int(psm)}"
        if charset:
            config += f" -c tessedit_char_whitelist={charset}"
        # image_to_string is the authoritative raw engine output.  Do not join,
        # strip, normalize, or filter it before storing RawOcrResult.text.
        started = time.perf_counter()
        text = self._pytesseract.image_to_string(prepared, lang=lang, config=config)
        data = self._pytesseract.image_to_data(
            prepared, lang=lang, config=config, output_type=self._pytesseract.Output.DICT
        )
        latency_ms = (time.perf_counter() - started) * 1000.0

        confidences = []
        for word, conf in zip(data.get("text", []), data.get("conf", []), strict=False):
            try:
                value = float(conf)
            except (TypeError, ValueError):
                continue
            if value < 0 or not word.strip():
                continue
            confidences.append(value / 100.0)

        confidence = float(np.mean(confidences)) if confidences else None
        # `text` stays exactly what the engine produced. The declared charset is
        # enforced downstream, in pipeline._read_region, so raw_ocr stays raw.
        honoured = not charset or all(c in set(charset) for c in text)
        return RawOcrResult(
            text=text,
            confidence=confidence,
            engine=self.name,
            engine_version=self.version,
            params={
                "lang": lang,
                "psm": int(psm),
                "oem": self.oem,
                "preprocessing": "none",
                "charset_size": len(charset),
                "tessdata_dir": self.tessdata_dir,
                "input_crop_shape": list(prepared.shape),
                "latency_ms_after_warmup": round(latency_ms, 3),
            },
            word_confidences=tuple(round(c, 4) for c in confidences),
            notes=() if honoured else ("engine_ignored_whitelist",),
        )


class TesseractPsmSweepEngine:
    """Evaluate several Tesseract PSMs on the same untouched field crop.

    Every attempt is retained in ``params.attempts``. Selection uses only the
    engine outputs and the declared field kind; Ground Truth is never consulted.
    """

    name = "tesseract"

    def __init__(self, *args, psm_candidates=(6, 7, 8, 11, 13), **kwargs):
        self.base = TesseractEngine(*args, **kwargs)
        self.psm_candidates = tuple(int(value) for value in psm_candidates)
        self.version = self.base.version
        self.tessdata_dir = self.base.tessdata_dir
        self.runtime_versions = self.base.runtime_versions

    @staticmethod
    def _selection_key(result, region_name, charset):
        kept = "".join(char for char in result.text if not charset or char in set(charset))
        compact = "".join(kept.split())
        confidence = -1.0 if result.confidence is None else result.confidence
        if region_name == "national_id_number":
            return (-abs(len(compact) - 14), confidence, len(compact))
        if region_name == "card_serial_number":
            in_range = 6 <= len(compact) <= 20
            return (int(in_range), len(compact), confidence)
        return (confidence, len(compact), -abs(len(compact) - 12))

    def recognize(self, image, charset="", lang="eng", psm=7, region_name=""):
        attempts = [
            self.base.recognize(image, charset=charset, lang=lang, psm=candidate, region_name=region_name)
            for candidate in self.psm_candidates
        ]
        chosen = max(
            enumerate(attempts),
            key=lambda item: self._selection_key(item[1], region_name, charset),
        )[0]
        result = attempts[chosen]
        attempt_records = [
            {
                "psm": candidate,
                "text": attempt.text,
                "confidence": attempt.confidence,
                "word_confidences": list(attempt.word_confidences),
                "latency_ms_after_warmup": attempt.params.get("latency_ms_after_warmup"),
            }
            for candidate, attempt in zip(self.psm_candidates, attempts, strict=False)
        ]
        params = dict(result.params)
        params.update(
            {
                "psm_candidates": list(self.psm_candidates),
                "selected_psm": self.psm_candidates[chosen],
                "selection_without_ground_truth": True,
                "attempts": attempt_records,
                "latency_ms_after_warmup": round(
                    sum(attempt.params.get("latency_ms_after_warmup", 0.0) for attempt in attempts), 3
                ),
            }
        )
        return RawOcrResult(
            text=result.text,
            confidence=result.confidence,
            engine=result.engine,
            engine_version=result.engine_version,
            params=params,
            word_confidences=result.word_confidences,
            notes=result.notes + ("psm_sweep_selected_without_ground_truth",),
        )

    def warmup(self):
        """Warm every timed PSM/language path before the measured run."""
        blank = np.zeros((64, 320, 3), dtype=np.uint8)
        for lang, charset in (("ara", CHARSETS["digits"]), ("eng", CHARSETS["latin_serial"])):
            for psm in self.psm_candidates:
                self.base.recognize(blank, charset=charset, lang=lang, psm=psm, region_name="warmup")


def _model_file_hashes(model_dir):
    hashes = {}
    for path in sorted(Path(model_dir).glob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        hashes[path.name] = digest.hexdigest()
    return hashes


class PaddleRecognitionEngine:
    """Recognition-only PaddleOCR with explicit local Arabic/Latin models."""

    name = "paddleocr"

    def __init__(
        self,
        arabic_model_dir,
        latin_model_dir,
        cache_dir=None,
        device="cpu",
        arabic_model_name="arabic_PP-OCRv3_mobile_rec",
        latin_model_name="latin_PP-OCRv3_mobile_rec",
    ):
        if cache_dir:
            os.environ["PADDLE_PDX_CACHE_HOME"] = str(cache_dir)
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        import paddle
        import paddleocr
        from paddleocr import TextRecognition

        self.model_dirs = {
            "arabic": str(arabic_model_dir),
            "latin": str(latin_model_dir),
        }
        self.model_names = {"arabic": arabic_model_name, "latin": latin_model_name}
        for label, path in self.model_dirs.items():
            if not os.path.isdir(path):
                raise FileNotFoundError(f"explicit PaddleOCR {label} model not found: {path}")
        self._models = {
            "ara": TextRecognition(model_name=arabic_model_name, model_dir=self.model_dirs["arabic"], device=device),
            "eng": TextRecognition(model_name=latin_model_name, model_dir=self.model_dirs["latin"], device=device),
        }
        self.version = str(paddleocr.__version__)
        self.runtime_versions = {
            "paddleocr": self.version,
            "paddlepaddle": str(paddle.__version__),
        }
        self.device = device
        self.model_provenance = {
            label: {
                "model_name": self.model_names[label],
                "model_dir": self.model_dirs[label],
                "source_url": OFFICIAL_PADDLE_MODEL_SOURCES.get(self.model_names[label]),
                "files_sha256": _model_file_hashes(self.model_dirs[label]),
            }
            for label in ("arabic", "latin")
        }

    def warmup(self):
        """Warm both explicit local recognition models before timing fields."""
        blank = np.zeros((64, 320, 3), dtype=np.uint8)
        for model in self._models.values():
            model.predict(input=blank, batch_size=1)[0]

    def recognize(self, image, charset="", lang="eng", psm=7, region_name=""):
        model_key = "eng" if lang == "eng" else "ara"
        label = "latin" if model_key == "eng" else "arabic"
        model_name = self.model_names[label]
        prepared = np.asarray(image)
        started = time.perf_counter()
        result = self._models[model_key].predict(input=prepared, batch_size=1)[0].json["res"]
        latency_ms = (time.perf_counter() - started) * 1000.0
        # rec_text is stored exactly as PaddleOCR returned it. Character
        # restrictions and normalization remain downstream operations.
        return RawOcrResult(
            text=result.get("rec_text", ""),
            confidence=float(result["rec_score"]) if result.get("rec_score") is not None else None,
            engine=self.name,
            engine_version=self.version,
            params={
                "mode": "recognition_only",
                "model_name": model_name,
                "model_dir": self.model_dirs[label],
                "model_source_url": self.model_provenance[label]["source_url"],
                "model_files_sha256": self.model_provenance[label]["files_sha256"],
                "device": self.device,
                "preprocessing": "none_outside_model_required_input_transform",
                "batch_size": 1,
                "input_crop_shape": list(prepared.shape),
                "latency_ms_after_warmup": round(latency_ms, 3),
                "psm": "not_applicable",
                "requested_region_psm": int(psm),
            },
            notes=("explicit_local_recognition_model",),
        )


def charset_for(content):
    """Character set the config's content type permits."""
    return CHARSETS.get(content, CHARSETS["arabic_text"])


def lang_for(content):
    """Tesseract language for a content type."""
    return LANG_BY_CONTENT.get(content, "ara")
