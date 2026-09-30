"""Prove the service image holds no data: list every file, scan every file, in every layer.

    python scripts/phase4_service_image_scan.py --rootfs ROOTFS.tar --save IMAGE.tar --base BASE.tar \
        --anchor-site-packages DIR --model-manifest docker/model_files.sha256 --out DIR

ROOTFS.tar is `docker export` of a container of the image (the merged
filesystem). IMAGE.tar is `docker save` of the image: every layer, so a file
added in one layer and deleted in a later one is still seen, plus the config and
history. BASE.tar is `docker export` of the pinned base image. Declared in
configs/phase4_service_rule_v1.json `image_scan`, amendments 1, 4 and 5.

What fails the scan:
- the config is missing, or a layer the config lists was not opened, or a
  merged file was not seen (same path, same sha256) in any scanned layer;
- inside /app: any image or PDF file, and any structurally valid 14-digit
  sequence, in any encoding searched. The exception is the ASCII runs amendment 1
  S2 allows: inside a hex hash, or a decimal literal of a model file whose
  sha256 is the pinned one;
- anywhere, in any layer:
  - a font that names a Microsoft family, copyright, manufacturer or vendor;
  - a font whose names cannot be read;
  - a pip or root cache;
- in the config or history: a secret-like variable, an API key value, or a
  structurally valid 14-digit sequence outside a hex digest;
- a site-packages file that:
  - no RECORD covers, bytecode aside;
  - differs from its RECORD, unless it is one of the OpenCV files the headless
    reinstall replaced (D-046), or bytecode whose code object equals a compile of
    its RECORD-verified source (amendment 9 AA2);
  - is bytecode that RECORD gives no hash (or no row), and whose code object
    does not equal a compile of its RECORD-verified source (amendment 10 AB1);
  - has, in any layer, a version that is neither the image's final file nor the
    base image's file at that path (amendment 11 AB3);
  - is absent from, or differs from, the independently built anchor venv;
  - belongs to a distribution whose RECORD rows differ from the anchor's;
- a file outside /app and site-packages, in any layer, that holds an image, a
  font or a structurally valid sequence, and is not the base image's or dpkg's.

Encodings searched for digit sequences:
- ASCII runs, every 14-digit window;
- maximal runs of single digits joined by one consistent separator, and digit
  groups joined by one separator that total exactly 14 digits;
- Arabic-Indic and Persian digits in UTF-8, UTF-16 and UTF-32;
- lists of 14 integers 0-9 in JSON;
- in .npy files and .npz, zip and gzip members:
  - numeric arrays as numbers: rows of 14 digits, 1-D digit vectors, and
    14-digit integer values; their bytes are never read as text;
  - string arrays as text.

HDF5 weights are opaque: each one inside /app is pinned by sha256, and the rest
are RECORD- and anchor-verified wheel content.

Only paths, counts and kinds are written; never a digit sequence.
"""
from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import importlib.util
import io
import json
import marshal
import math
import re
import struct
import sys
import tarfile
import zipfile
import zlib
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SITE = "usr/local/lib/python3.11/site-packages/"
ASCII_RUN = re.compile(rb"[0-9]{14,}")
SEPARATORS = (b", ", b",", b" ", b";", b"|", b"\t", b"-", b".", b"/", b":")
SEPARATED_RUNS = tuple(re.compile(rb"[0-9](?:" + re.escape(sep) + rb"[0-9]){13,}") for sep in SEPARATORS)
#: an ID written in groups ("2 990101 01 1234 5"): digit groups joined by one separator, exactly 14 digits
GROUPED_RUNS = tuple(re.compile(rb"(?<![0-9])[0-9]+(?:" + re.escape(sep) + rb"[0-9]+)+(?![0-9])")
                     for sep in SEPARATORS)
UTF8_INDIC = re.compile(rb"(?:\xd9[\xa0-\xa9]|\xdb[\xb0-\xb9]){14,}")
UTF16LE = re.compile(rb"(?:[0-9]\x00|[\x60-\x69]\x06|[\xf0-\xf9]\x06){14,}")
UTF16BE = re.compile(rb"(?:\x00[0-9]|\x06[\x60-\x69]|\x06[\xf0-\xf9]){14,}")
UTF32LE = re.compile(rb"(?:[0-9]\x00\x00\x00|[\x60-\x69]\x06\x00\x00|[\xf0-\xf9]\x06\x00\x00){14,}")
UTF32BE = re.compile(rb"(?:\x00\x00\x00[0-9]|\x00\x00\x06[\x60-\x69]|\x00\x00\x06[\xf0-\xf9]){14,}")
HEX_CHARS = frozenset(b"0123456789abcdefABCDEF")
LITERAL_CHARS = frozenset(b"0123456789.eE+-")
DECIMAL = re.compile(rb"[-+]?[0-9]*\.[0-9]+(?:[eE][-+]?[0-9]+)?")
NPY_MAGIC = b"\x93NUMPY"
IMAGE_BRANDS = frozenset({b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"hevm", b"hevs", b"mif1",
                          b"msf1", b"mif2", b"avif", b"avis", b"avio", b"miaf"})
IMAGE_EXTENSIONS = {".ico": "ico", ".cur": "ico", ".svg": "svg", ".pnm": "pnm", ".ppm": "pnm", ".pgm": "pnm",
                    ".pbm": "pnm", ".jxl": "jpeg-xl", ".tga": "tga", ".xcf": "xcf"}
FONT_EXTENSIONS = {".ttf", ".otf", ".ttc", ".woff", ".woff2", ".pfb", ".pfa", ".fon", ".pcf", ".bdf", ".eot"}
MICROSOFT_FAMILIES = ("arial", "times new roman", "tahoma", "segoe", "calibri", "cambria", "consolas",
                      "courier new", "verdana", "georgia", "trebuchet", "traditional arabic", "sakkal majalla",
                      "arabic typesetting", "simplified arabic", "andalus", "microsoft", "aldhabi",
                      "urdu typesetting", "leelawadee", "nirmala", "gadugi", "ebrima", "candara", "constantia",
                      "corbel", "lucida console", "lucida sans unicode", "impact", "comic sans", "webdings",
                      "wingdings", "marlett", "mongolian baiti", "sylfaen", "mv boli", "palatino linotype",
                      "franklin gothic", "sitka", "bahnschrift", "ink free", "javanese text", "myanmar text")
MICROSOFT_WORDS = re.compile(r"\b(?:" + "|".join(re.escape(f) for f in MICROSOFT_FAMILIES) + r")\b")
MAX_NESTED_BYTES = 256 * 1024 * 1024
#: files the container runtime writes when a container is created (amendment 5 W1; dev/console amendment 9 AA1)
RUNTIME_FILES = frozenset({".dockerenv", "etc/hostname", "etc/hosts", "etc/resolv.conf", "dev/console"})
#: the code-object fields compared for regenerated bytecode (amendment 9 AA2)
CODE_FIELDS = ("co_code", "co_names", "co_varnames", "co_freevars", "co_cellvars", "co_name", "co_qualname",
               "co_filename", "co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_nlocals",
               "co_stacksize", "co_flags", "co_firstlineno", "co_linetable", "co_exceptiontable")
INSTALLER_FILES = frozenset({"INSTALLER", "REQUESTED", "RECORD", "direct_url.json"})


# --------------------------------------------------------------------------- national-ID structure

def could_be_an_id(digits: str) -> bool:
    """True when a 14-digit sequence is structurally valid, the only kind a real ID can be."""
    from src.extraction.admission_second_pass import structurally_valid

    return len(digits) == 14 and structurally_valid(digits)


def fold(text: str) -> str:
    out = []
    for ch in text:
        if "٠" <= ch <= "٩":
            out.append(str(ord(ch) - 0x660))
        elif "۰" <= ch <= "۹":
            out.append(str(ord(ch) - 0x6F0))
        elif ch.isdigit():
            out.append(ch)
    return "".join(out)


def windows(digits: str):
    """Every 14-digit window of a longer run (a run of 20 holds 7 candidate IDs)."""
    for i in range(len(digits) - 13):
        yield digits[i:i + 14]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- formats

def image_kind(path: str, data: bytes) -> str | None:
    head = data[:32]
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:2] == b"BM" and len(head) >= 26 and int.from_bytes(head[2:6], "little") in range(26, len(data) + 1) \
            and int.from_bytes(head[14:18], "little") in (12, 40, 52, 56, 64, 108, 124):
        return "bmp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[4:8] == b"ftyp" and (head[8:12] in IMAGE_BRANDS
                                 or any(head[i:i + 4] in IMAGE_BRANDS for i in range(16, 32, 4))):
        return "heif/avif"
    if head[:12] == b"\x00\x00\x00\x0cjP  \r\n\x87\n":
        return "jpeg2000"
    if head[:12] == b"\x00\x00\x00\x0cJXL \r\n\x87\n":
        return "jpeg-xl"
    if head[:4] == b"8BPS":
        return "psd"
    if head[:4] == b"\x76\x2f\x31\x01":
        return "openexr"
    if b"%PDF-" in data[:1024]:
        return "pdf"
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return IMAGE_EXTENSIONS[suffix]
    if b"<svg" in data[:2048].lower():
        return "svg"
    return None


def font_kind(path: str, data: bytes) -> str | None:
    head = data[:4]
    if head in (b"\x00\x01\x00\x00", b"true"):
        return "truetype"
    if head == b"OTTO":
        return "opentype"
    if head == b"ttcf":
        return "truetype-collection"
    if head == b"wOFF":
        return "woff"
    if head == b"wOF2":
        return "woff2"
    if PurePosixPath(path).suffix.lower() in FONT_EXTENSIONS:
        return "font-by-extension"
    return None


def sfnt_tables(data: bytes, offset: int = 0) -> dict[bytes, bytes]:
    count = struct.unpack(">H", data[offset + 4:offset + 6])[0]
    tables = {}
    for i in range(count):
        tag, _check, start, length = struct.unpack(">4sIII", data[offset + 12 + 16 * i:offset + 28 + 16 * i])
        tables[tag] = data[start:start + length]
    return tables


def woff_tables(data: bytes) -> dict[bytes, bytes]:
    count = struct.unpack(">H", data[12:14])[0]
    tables = {}
    for i in range(count):
        tag, start, comp, orig, _check = struct.unpack(">4sIIII", data[44 + 20 * i:64 + 20 * i])
        raw = data[start:start + comp]
        tables[tag] = zlib.decompress(raw) if comp < orig else raw
    return tables


def names_of(tables: dict[bytes, bytes]) -> tuple[list[str], str]:
    name = tables.get(b"name", b"")
    _fmt, count, strings = struct.unpack(">HHH", name[:6])
    found = []
    for i in range(count):
        platform, _enc, _lang, name_id, length, offset = struct.unpack(">HHHHHH", name[6 + 12 * i:18 + 12 * i])
        if name_id not in (0, 1, 4, 8, 9):
            continue
        raw = name[strings + offset:strings + offset + length]
        found.append(raw.decode("utf-16-be", "replace") if platform in (0, 3) else raw.decode("latin-1"))
    os2 = tables.get(b"OS/2", b"")
    return found, os2[58:62].decode("latin-1") if len(os2) >= 62 else ""


def font_names(kind: str, data: bytes) -> list[tuple[list[str], str]] | None:
    """Per font: name-table strings (IDs 0, 1, 4, 8, 9) and the OS/2 vendor. None if unreadable.
    A TrueType collection gives one entry per font it holds (amendment 5 W7)."""
    try:
        if kind in ("truetype", "opentype"):
            return [names_of(sfnt_tables(data))]
        if kind == "truetype-collection":
            count = struct.unpack(">I", data[8:12])[0]
            offsets = [struct.unpack(">I", data[12 + 4 * i:16 + 4 * i])[0] for i in range(count)]
            return [names_of(sfnt_tables(data, offset)) for offset in offsets]
        if kind == "woff":
            return [names_of(woff_tables(data))]
        return None
    except (struct.error, zlib.error, IndexError):
        return None


def is_microsoft(names: list[str], vendor: str) -> bool:
    text = " ".join(names).lower()
    return vendor.strip().upper() in ("MS", "MSFT") or bool(MICROSOFT_WORDS.search(text))


# --------------------------------------------------------------------------- digit sequences in every encoding

def classify_ascii(data: bytes, start: int, end: int) -> str:
    hs, he = start, end
    while hs > 0 and data[hs - 1] in HEX_CHARS:
        hs -= 1
    while he < len(data) and data[he] in HEX_CHARS:
        he += 1
    token = data[hs:he]
    if len(token) in (40, 64) and not token.isdigit():
        return "inside_hex_hash"
    ls, le = start, end
    while ls > 0 and data[ls - 1] in LITERAL_CHARS:
        ls -= 1
    while le < len(data) and data[le] in LITERAL_CHARS:
        le += 1
    if DECIMAL.fullmatch(data[ls:le]):
        return "inside_decimal_literal"
    return "other"


def npy_parts(data: bytes) -> tuple[bytes, str, tuple, bytes] | None:
    """(header bytes, dtype descr, shape, body) of a .npy file, or None."""
    import ast

    if data[:6] != NPY_MAGIC or len(data) < 12:
        return None
    major = data[6]
    header_start = 10 if major == 1 else 12
    header_len = struct.unpack("<H", data[8:10])[0] if major == 1 else struct.unpack("<I", data[8:12])[0]
    header_bytes = data[:header_start + header_len]
    try:
        header = ast.literal_eval(data[header_start:header_start + header_len].decode("latin-1"))
        return header_bytes, str(header["descr"]), tuple(header["shape"]), data[header_start + header_len:]
    except (ValueError, SyntaxError, KeyError, TypeError):
        return None


def npy_numeric_sequences(data: bytes) -> list[str]:
    """A numeric array read as numbers: rows of 14 digits, 1-D digit vectors, 14-digit values."""
    import numpy as np

    try:
        array = np.load(io.BytesIO(data), allow_pickle=False)     # bytes already in memory (in_memory_decode)
    except (ValueError, OSError, EOFError):
        return []
    out = []
    if array.dtype.kind not in "iubf" or not array.size:
        return out
    flat = array.ravel().astype(np.float64)
    # 14-digit values, integer or whole float
    mask = (flat >= 1e13) & (flat < 1e14) & (flat == np.floor(flat))
    out.extend(str(int(v)) for v in flat[mask])
    # digits as values: rows along a last axis of 14, and 1-D digit vectors (amendment 6 X4: floats too)
    whole = array.astype(np.float64)
    if not np.all(whole == np.floor(whole)):
        return out
    values = whole.astype(np.int64)
    if array.ndim >= 1 and array.shape[-1] == 14:
        for row in values.reshape(-1, 14):
            if row.min() >= 0 and row.max() <= 9:
                out.append("".join(str(int(v)) for v in row))
    if array.ndim == 1 and values.min() >= 0 and values.max() <= 9:
        out.extend(windows("".join(str(int(v)) for v in values)))
    return out


def npy_body_is_numeric(descr: str) -> tuple[bool, int]:
    """(numeric, itemsize) for a .npy descr; object, structured and string arrays are not numeric."""
    import numpy as np

    try:
        dtype = np.dtype(descr if not descr.startswith("[") else eval_descr(descr))
    except (TypeError, ValueError, SyntaxError):
        return False, 0
    return dtype.kind in "iubf", dtype.itemsize


def eval_descr(descr: str):
    import ast

    return ast.literal_eval(descr)


def text_sequences(data: bytes, pinned_ok: bool) -> list[tuple[str, str, str]]:
    """(encoding, kind, digits) found by the byte-level searches."""
    found = []
    for match in ASCII_RUN.finditer(data):
        how = classify_ascii(data, match.start(), match.end())
        for digits in windows(match.group().decode()):
            found.append(("ascii", how, digits))
    for regex in SEPARATED_RUNS:
        for match in regex.finditer(data):
            digits = bytes(c for c in match.group() if 48 <= c <= 57).decode()
            for window in windows(digits):
                found.append(("ascii_separated", "other", window))
    for regex in GROUPED_RUNS:
        for match in regex.finditer(data):
            digits = bytes(c for c in match.group() if 48 <= c <= 57).decode()
            if len(digits) == 14:
                found.append(("ascii_grouped", "other", digits))
    for regex, encoding, codec in ((UTF8_INDIC, "utf8_indic", "utf-8"), (UTF16LE, "utf16le", "utf-16-le"),
                                   (UTF16BE, "utf16be", "utf-16-be"), (UTF32LE, "utf32le", "utf-32-le"),
                                   (UTF32BE, "utf32be", "utf-32-be")):
        for match in regex.finditer(data):
            for digits in windows(fold(match.group().decode(codec, "replace"))):
                found.append((encoding, "other", digits))
    return found


def json_sequences(value) -> list[str]:
    out = []
    if isinstance(value, list):
        if len(value) == 14 and all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 9 for v in value):
            out.append("".join(str(v) for v in value))
        for v in value:
            out.extend(json_sequences(v))
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(json_sequences(v))
    return out


def sequences(path: str, data: bytes, pinned_ok: bool, depth: int = 0) -> list[tuple[str, str, str]]:
    """(encoding, kind, digits) for every 14-digit sequence found. `kind` is the amendment-1 class."""
    parts = npy_parts(data) if data[:6] == NPY_MAGIC else None
    if parts is not None:
        header, descr, shape, body = parts
        found = text_sequences(header, pinned_ok)
        numeric, itemsize = npy_body_is_numeric(descr)
        if not numeric:
            # string, object (pickled) and structured arrays: the body is searched as text (amendment 6 X1)
            found.extend(("npy_" + encoding, kind, digits) for encoding, kind, digits in text_sequences(body, False))
            return found
        # a numeric array's bytes are never read as text (amendment 5 W2); it is read as numbers
        found.extend(("npy_numeric", "other", digits) for digits in npy_numeric_sequences(data))
        count = 1
        for n in shape:
            count *= int(n)
        trailing = body[count * itemsize:]
        if trailing:                      # bytes after the array body are searched as text (amendment 6 X2)
            found.extend(("npy_trailing_" + encoding, kind, digits)
                          for encoding, kind, digits in text_sequences(trailing, False))
        return found
    found = text_sequences(data, pinned_ok)
    if PurePosixPath(path).suffix.lower() == ".json":
        try:
            found.extend(("json_int_list", "other", d) for d in json_sequences(json.loads(data.decode("utf-8"))))
        except (ValueError, UnicodeDecodeError):
            pass
    if depth < 2 and len(data) <= MAX_NESTED_BYTES:
        for name, member in nested_members(path, data):
            found.extend((f"{encoding}@{PurePosixPath(name).suffix or 'member'}", kind, digits)
                         for encoding, kind, digits in sequences(name, member, False, depth + 1))
    return found


def nested_members(path: str, data: bytes):
    """(name, bytes) of zip (npz, whl, jar) and gzip members, for the recursive scan."""
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for info in archive.infolist():
                    if not info.is_dir() and info.file_size <= MAX_NESTED_BYTES:
                        yield info.filename, archive.read(info)
        except (zipfile.BadZipFile, OSError, NotImplementedError, RuntimeError):
            return
    elif data[:2] == b"\x1f\x8b":
        try:
            yield PurePosixPath(path).stem, gzip.decompress(data)[:MAX_NESTED_BYTES]
        except (OSError, EOFError, zlib.error):
            return


def nested_images(path: str, data: bytes, depth: int = 0) -> list[str]:
    out = []
    if depth < 2:
        for name, member in nested_members(path, data):
            kind = image_kind(name, member)
            if kind:
                out.append(f"{kind}@{name}")
            out.extend(nested_images(name, member, depth + 1))
    return out


# --------------------------------------------------------------------------- one file

class Findings:
    def __init__(self, pinned: dict[str, str]):
        #: path (no leading slash) -> the sha256 docker/model_files.sha256 pins for it
        self.pinned = dict(pinned)
        self.images, self.fonts = [], []
        self.app_sequences = defaultdict(Counter)
        self.valid_outside_app = defaultdict(Counter)
        self.sequence_counts = Counter()
        self.failures: list[str] = []

    def inspect(self, path: str, data: bytes, area: str, where: str) -> dict:
        """Scan one file. Returns what it held, for provenance checks."""
        held = {"image": None, "font": None, "valid_sequences": 0}
        kind = image_kind(path, data)
        nested = nested_images(path, data)
        if kind or nested:
            held["image"] = kind or nested[0]
            self.images.append({"path": path, "kind": kind, "nested": nested[:5], "area": area, "where": where})
            if area == "app":
                self.failures.append(f"image or PDF in /app ({where}): {path}")
        fkind = font_kind(path, data)
        if fkind:
            fonts = font_names(fkind, data)
            microsoft = fonts is not None and any(is_microsoft(*names) for names in fonts)
            families = sorted({n for names, _ in (fonts or []) for n in names})[:4]
            self.fonts.append({"path": path, "kind": fkind, "where": where, "names_readable": fonts is not None,
                               "fonts": len(fonts or []), "microsoft": microsoft, "families": families})
            held["font"] = fkind
            if microsoft:
                self.failures.append(f"a Microsoft font ({where}): {path}")
            elif fonts is None:
                self.failures.append(f"a font whose names cannot be read ({where}, {fkind}): {path}")
        pinned_ok = path in self.pinned and sha256(data) == self.pinned[path]      # amendment 5 W5
        for encoding, how, digits in sequences(path, data, pinned_ok):
            valid = could_be_an_id(digits)
            self.sequence_counts[f"{area}:{encoding}:{'valid' if valid else 'impossible'}"] += 1
            if area == "app":
                self.app_sequences[path][f"{encoding}:{how}:{'valid' if valid else 'impossible'}"] += 1
                allowed_by_s2 = encoding == "ascii" and (
                    how == "inside_hex_hash" or (how == "inside_decimal_literal" and pinned_ok))
                if valid and not allowed_by_s2:
                    self.failures.append(f"a structurally valid 14-digit sequence in /app ({where}, "
                                         f"{encoding}, {how}): {path}")
            elif valid:
                held["valid_sequences"] += 1
                self.valid_outside_app[area][path] += 1
        return held


def area_of(path: str) -> str:
    if path.startswith("app/"):
        return "app"
    if path.startswith(SITE):
        return "site-packages"
    return "system"


def record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def source_of(rel: str) -> str | None:
    """`pkg/__pycache__/mod.cpython-311[.opt-N].pyc` -> `pkg/mod.py`; None for any other shape."""
    path = PurePosixPath(rel)
    if path.suffix != ".pyc" or path.parent.name != "__pycache__":
        return None
    stem = path.name.split(".")[0]
    return str(path.parent.parent / (stem + ".py"))


def same_constant(a, b) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, type(same_constant.__code__)):
        return same_code(a, b)
    if isinstance(a, tuple):
        return len(a) == len(b) and all(same_constant(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and math.isnan(a):
        return math.isnan(b)
    if isinstance(a, complex):
        return same_constant(a.real, b.real) and same_constant(a.imag, b.imag)
    return a == b


def same_code(a, b) -> bool:
    """Structural, recursive equality of two code objects (amendment 9 AA2)."""
    if any(getattr(a, f) != getattr(b, f) for f in CODE_FIELDS):
        return False
    return len(a.co_consts) == len(b.co_consts) and all(
        same_constant(x, y) for x, y in zip(a.co_consts, b.co_consts))


def bytecode_matches_source(rel: str, pyc: bytes, source: bytes) -> bool:
    """True only when the .pyc's code object equals a compile of its source in this interpreter.

    The header must carry this interpreter's magic number, and the code object must
    end the file; the optimisation level
    follows the file name; the file name compiled in is the file's path in the image.
    """
    if len(pyc) < 16 or pyc[:4] != importlib.util.MAGIC_NUMBER:
        return False
    name = PurePosixPath(rel).name
    optimize = 2 if ".opt-2." in name else 1 if ".opt-1." in name else 0
    try:
        body = io.BytesIO(pyc[16:])
        stored = marshal.load(body)
        if body.tell() != len(pyc) - 16:  # bytes after the code object are not bytecode
            return False
        fresh = compile(source, "/" + SITE + source_of(rel), "exec", dont_inherit=True, optimize=optimize)
    except (ValueError, EOFError, TypeError, SyntaxError):
        return False
    return isinstance(stored, type(fresh)) and same_code(stored, fresh)


def members(tar_path: str):
    with tarfile.open(tar_path, mode="r|*") as archive:
        for member in archive:
            if member.isfile():
                yield member.name.removeprefix("./"), archive.extractfile(member).read()


def config_failures(config: dict) -> list[str]:
    """Amendment 5 W4: the config and history, decoded, every 14-digit window tested."""
    problems = []
    for env in config["config"].get("Env") or []:
        if env.split("=", 1)[0].upper().endswith(("API_KEY", "SECRET", "TOKEN", "PASSWORD")):
            problems.append("a secret-like variable in the image environment")
    text = json.dumps(config, ensure_ascii=False)
    if re.search(r"OCR_API_KEY=\S", text):
        problems.append("an API key value in the config or history")
    for _encoding, how, digits in text_sequences(text.encode("utf-8"), False):
        if how != "inside_hex_hash" and could_be_an_id(digits):
            problems.append("a structurally valid 14-digit sequence in the config or history")
            break
    return problems


# --------------------------------------------------------------------------- the passes

class HashingReader:
    """A layer blob read as a stream: decompressed if gzip, and the uncompressed bytes hashed.

    The sha256 of a layer's uncompressed tar is its diff_id, so the config's diff_ids
    can be matched against what was actually opened (amendment 7 Y2).
    """

    def __init__(self, raw):
        head = raw.read(2)
        rest = PrefixedReader(head, raw)
        self.stream = gzip.GzipFile(fileobj=rest) if head == b"\x1f\x8b" else rest
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        data = self.stream.read(size)
        self.digest.update(data)
        return data

    def finish(self) -> str:
        """Read what tarfile left unread (the end-of-archive padding) and return the hex digest."""
        try:
            while self.read(1 << 20):
                pass
        except (OSError, EOFError, zlib.error):
            pass
        return self.digest.hexdigest()


class PrefixedReader:
    def __init__(self, prefix: bytes, raw):
        self.prefix, self.raw = prefix, raw

    def read(self, size: int = -1) -> bytes:
        if self.prefix:
            if size is None or size < 0:
                data, self.prefix = self.prefix + self.raw.read(), b""
                return data
            data, self.prefix = self.prefix[:size], self.prefix[size:]
            if len(data) < size:
                data += self.raw.read(size - len(data))
            return data
        return self.raw.read(size)


def base_hashes(base_tar: str) -> dict[str, str]:
    return {path: sha256(data) for path, data in members(base_tar)}


def scan_save(save_tar: str, findings: Findings) -> tuple[dict, dict, dict]:
    """Every layer of `docker save`, and the config: a file later deleted is still scanned.

    Returns the summary, the system paths that held something (with their sha256 in each
    layer), and every (path -> sha256s) any scanned layer held, for the merged pass.
    """
    summary = Counter()
    system_held: dict[str, list[str]] = {}
    layer_seen: dict[str, set[str]] = defaultdict(set)
    diff_ids = None
    opened: set[str] = set()
    with tarfile.open(save_tar, mode="r:*") as outer:
        for member in outer.getmembers():
            if not member.isfile():
                continue
            if member.size < 4 * 1024 * 1024:
                blob = outer.extractfile(member).read()
                if blob[:1] == b"{":
                    try:
                        config = json.loads(blob)
                    except ValueError:
                        config = None
                    if isinstance(config, dict) and "config" in config and "rootfs" in config:
                        diff_ids = list(config["rootfs"].get("diff_ids") or [])
                        findings.failures.extend(config_failures(config))
                        summary["config_history_entries"] = len(config.get("history", []))
                        summary["config_env_names"] = len(config["config"].get("Env") or [])
                    continue
                fileobj = io.BytesIO(blob)
            else:
                fileobj = outer.extractfile(member)
            stream = HashingReader(fileobj)
            try:
                layer = tarfile.open(fileobj=stream, mode="r|")
            except tarfile.TarError:
                summary["blobs_not_a_tar"] += 1
                continue
            summary["layers"] += 1
            local: dict[str, str] = {}
            with layer:
                for entry in layer:
                    path = entry.name.removeprefix("./")
                    if PurePosixPath(path).name.startswith(".wh."):
                        summary["whiteouts"] += 1                # a marker, not a file (amendment 5 W7)
                        continue
                    if "/.cache/pip" in "/" + path or path.startswith("root/.cache"):
                        findings.failures.append(f"a pip or root cache file in a layer: {path}")
                    if entry.islnk():
                        target = entry.linkname.removeprefix("./")
                        if target in local:
                            layer_seen[path].add(local[target])
                        continue
                    if not entry.isfile():
                        continue
                    summary["layer_files"] += 1
                    data = layer.extractfile(entry).read()
                    digest = sha256(data)
                    local[path] = digest
                    layer_seen[path].add(digest)
                    area = area_of(path)
                    held = findings.inspect(path, data, area, "layer")
                    if area == "system" and (held["image"] or held["font"] or held["valid_sequences"]):
                        system_held.setdefault(path, []).append(digest)
            opened.add("sha256:" + stream.finish())
    if diff_ids is None:
        findings.failures.append("no image config was found in the saved image")
    else:
        # every listed layer (a diff_id is the sha256 of the uncompressed layer tar) was opened (amendment 7 Y2)
        missing = sorted(set(diff_ids) - opened)
        if missing:
            findings.failures.append(f"{len(missing)} listed layer(s) not opened and scanned (amendment 7 Y2)")
    summary["diff_ids"] = len(diff_ids or [])
    summary["diff_ids_opened"] = len(set(diff_ids or []) & opened)
    return dict(summary), system_held, layer_seen


def record_rows(data: bytes) -> dict[str, str]:
    """A RECORD's comparable rows: path -> hash, bytecode, `..` rows and installer files left out."""
    rows = {}
    for row in csv.reader(io.StringIO(data.decode("utf-8", "replace"))):
        if len(row) < 2:
            continue
        path = row[0]
        if path.startswith("..") or path.endswith(".pyc") or "__pycache__/" in path:
            continue
        if PurePosixPath(path).name in INSTALLER_FILES and PurePosixPath(path).parent.name.endswith(".dist-info"):
            continue
        rows[path] = row[1]
    return rows


def scan_rootfs(rootfs_tar: str, findings: Findings, base: dict[str, str], anchor: Path | None,
                layer_held: dict[str, list[str]], layer_seen: dict[str, set[str]]) -> dict:
    """The merged filesystem: the listing, layer coverage, RECORD and anchor checks, system provenance."""
    files, site, merged = [], {}, {}
    records: dict[str, bytes] = {}
    dpkg_md5: dict[str, str] = {}
    system_md5: dict[str, str] = {}
    not_seen = []
    for path, data in members(rootfs_tar):
        files.append((path, len(data)))
        area = area_of(path)
        digest = sha256(data)
        merged[path] = digest
        if digest not in layer_seen.get(path, ()) and not (path in RUNTIME_FILES and len(data) < 4096):
            not_seen.append(path)
        if area == "site-packages":
            rel = path[len(SITE):]
            site[rel] = record_hash(data)
            if PurePosixPath(rel).name == "RECORD" and rel.split("/")[0].endswith(".dist-info"):
                records[rel] = data
        if path.startswith("var/lib/dpkg/info/") and path.endswith(".md5sums"):
            for line in data.decode("utf-8", "replace").splitlines():
                parts = line.split(None, 1)
                if len(parts) == 2:
                    dpkg_md5[parts[1].strip()] = parts[0]
        if area == "system" and path in layer_held:
            system_md5[path] = hashlib.md5(data).hexdigest()  # noqa: S324 - dpkg's own md5sums
    for path in not_seen[:50]:
        findings.failures.append(f"a merged file no scanned layer held (amendment 5 W1): {path}")

    # site-packages: RECORD coverage and hashes, `..` entries resolved against the merged filesystem
    covered, verified, mismatches, verified_rels, hashed_rels = set(), 0, [], set(), set()
    for record, data in records.items():
        for row in csv.reader(io.StringIO(data.decode("utf-8", "replace"))):
            if len(row) < 2:
                continue
            rel, digest = row[0], row[1]
            if rel.startswith(".."):
                resolved = []
                for part in PurePosixPath(SITE, rel).parts:
                    if part == "..":
                        resolved.pop()
                    else:
                        resolved.append(part)
                full = "/".join(resolved)
                covered.add(full)
                if digest and full in merged:
                    ok = digest == "sha256=" + base64.urlsafe_b64encode(bytes.fromhex(merged[full])).rstrip(
                        b"=").decode()
                    verified += ok
                    if not ok:
                        mismatches.append({"file": full, "record": record})
                continue
            rel = str(PurePosixPath(rel))
            covered.add(SITE + rel)
            if digest:
                hashed_rels.add(rel)
            if digest and rel in site:
                if site[rel] == digest:
                    verified += 1
                    verified_rels.add(rel)
                else:
                    mismatches.append({"file": rel, "record": record})
    headless = {}
    if "opencv_python_headless-4.10.0.84.dist-info/RECORD" in records:
        headless = record_rows(records["opencv_python_headless-4.10.0.84.dist-info/RECORD"])
    unexplained = [m for m in mismatches
                   if not (m["file"].startswith("cv2/") and headless.get(m["file"]) == site.get(m["file"]))]
    # a file byte-identical to the base image's own file at the same path is the base's (amendment 7 Y1)
    from_base = {rel for rel in site if base.get(SITE + rel) == merged[SITE + rel]}
    # regenerated bytecode: accepted only when it compiles back from its RECORD-verified source (amendment 9 AA2)
    wanted = {m["file"]: source_of(m["file"]) for m in unexplained
              if m["file"] in site and source_of(m["file"]) in verified_rels}
    # every other site-packages .pyc (RECORD gives it no hash, or no row) must compile back the same way
    # (amendment 10 AB1)
    unhashed_pyc = {rel for rel in site if rel.endswith(".pyc") and rel not in hashed_rels and rel not in from_base}
    no_source = sorted(rel for rel in unhashed_pyc if source_of(rel) not in verified_rels)
    wanted.update({rel: source_of(rel) for rel in unhashed_pyc if source_of(rel) in verified_rels})
    compiled_back = set()
    if wanted:
        needed = {SITE + r for r in wanted} | {SITE + src for src in wanted.values()}
        held = {path[len(SITE):]: data for path, data in members(rootfs_tar) if path in needed}
        compiled_back = {rel for rel, src in wanted.items()
                         if rel in held and src in held and bytecode_matches_source(rel, held[rel], held[src])}
    regenerated = {m["file"] for m in unexplained} & compiled_back
    unexplained = [m for m in unexplained if m["file"] not in regenerated]
    for m in unexplained[:50]:
        findings.failures.append(f"a site-packages file differs from its RECORD: {m['file']}")
    bytecode_failures = no_source + sorted(unhashed_pyc - compiled_back - set(no_source))
    for rel in bytecode_failures[:50]:
        findings.failures.append(f"a site-packages bytecode file that does not compile back from a "
                                 f"RECORD-verified source (amendment 10 AB1): {rel}")
    # every site-packages version in any layer is the final file (checked above) or the base's (amendment 11 AB3)
    layer_only = sorted(path for path, versions in layer_seen.items() if path.startswith(SITE)
                        and versions - {merged.get(path), base.get(path)})
    for path in layer_only[:50]:
        findings.failures.append(f"a site-packages file version in a layer that is neither the image's final "
                                 f"file nor the base image's (amendment 11 AB3): {path[len(SITE):]}")
    uncovered = [rel for rel in site if SITE + rel not in covered and not rel.endswith(".pyc")
                 and rel not in from_base]
    for rel in uncovered[:50]:
        findings.failures.append(f"a site-packages file no RECORD covers: {rel}")

    # the external anchor: the service venv built independently from the same lock (amendment 5 W6)
    anchor_counts, anchor_problems = Counter(), []
    if anchor is not None:
        for rel, digest in site.items():
            if rel.endswith(".pyc") or "/__pycache__/" in rel or rel in from_base:
                continue
            installer_file = (PurePosixPath(rel).name in INSTALLER_FILES
                              and rel.split("/")[0].endswith(".dist-info"))
            local = anchor / rel
            if not local.is_file():
                anchor_counts["absent_in_anchor"] += 1
                if not installer_file:
                    anchor_problems.append(f"absent from the anchor venv: {rel}")
                continue
            same = record_hash(local.read_bytes()) == digest
            anchor_counts["equal" if same else "different"] += 1
            if not same and not installer_file:
                anchor_problems.append(f"differs from the anchor venv: {rel}")
        for rel, data in records.items():
            local = anchor / rel
            if not local.is_file():
                anchor_problems.append(f"a distribution absent from the anchor venv: {rel.split('/')[0]}")
                continue
            if record_rows(data) != record_rows(local.read_bytes()):
                anchor_problems.append(f"RECORD rows differ from the anchor venv: {rel.split('/')[0]}")
            else:
                anchor_counts["records_equal"] += 1
        for problem in anchor_problems[:60]:
            findings.failures.append("a site-packages file " + problem)

    # system files that held an image, a font or a possible ID, in any layer, must be the base's or dpkg's
    unverified = []
    for path, versions in layer_held.items():
        final = merged.get(path)
        if final is not None and final in versions and (
                base.get(path) == final or dpkg_md5.get(path) == system_md5.get(path)):
            if all(v == final or base.get(path) == v for v in versions):
                continue
        unverified.append(path)
        findings.failures.append(f"a system file holding an image, a font or a possible ID, not verified "
                                 f"against dpkg or the base image (or deleted in a later layer): {path}")
    return {"files": files, "site_files": len(site), "records": len(records), "record_verified": verified,
            "record_mismatches": len(mismatches),
            "record_mismatches_explained_by_the_headless_reinstall": len(mismatches) - len(unexplained)
            - len(regenerated),
            "record_mismatches_regenerated_bytecode": sorted(regenerated),
            "unhashed_bytecode": len(unhashed_pyc),
            "unhashed_bytecode_compiled_back": len(unhashed_pyc & compiled_back),
            "unhashed_bytecode_failures": len(bytecode_failures),
            "site_packages_layer_versions_unverified": len(layer_only),
            "not_covered_and_not_bytecode": len(uncovered),
            "anchor": dict(anchor_counts), "anchor_problems": anchor_problems[:40],
            "merged_files_not_seen_in_a_layer": len(not_seen),
            "system_files_holding_something": len(layer_held), "system_unverified": unverified[:40],
            "dpkg_md5_entries": len(dpkg_md5)}


def load_manifest(path: str) -> dict[str, str]:
    pinned = {}
    for line in open(path, encoding="ascii"):
        if line.strip():
            digest, name = line.split("  /", 1)
            pinned[name.strip()] = digest.strip()
    return pinned


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rootfs", required=True)
    parser.add_argument("--save", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--anchor-site-packages", default=None)
    parser.add_argument("--model-manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--image-ref", default=None, help="recorded in the summary (amendment 10 AB2)")
    parser.add_argument("--image-id", default=None, help="recorded in the summary (amendment 10 AB2)")
    parser.add_argument("--base-ref", default=None, help="recorded in the summary (amendment 10 AB2)")
    args = parser.parse_args()
    findings = Findings(load_manifest(args.model_manifest))
    base = base_hashes(args.base)
    layers, layer_held, layer_seen = scan_save(args.save, findings)
    merged = scan_rootfs(args.rootfs, findings, base,
                         Path(args.anchor_site_packages) if args.anchor_site_packages else None,
                         layer_held, layer_seen)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    listing = sorted(merged.pop("files"))
    with (out / "filelist.tsv").open("w", encoding="utf-8") as handle:
        for path, size in listing:
            handle.write(f"{size}\t{path}\n")
    app_files = [p for p, _ in listing if p.startswith("app/")]
    failures = sorted(set(findings.failures))
    summary = {
        "provenance": {"image_ref": args.image_ref, "image_id": args.image_id, "base_ref": args.base_ref},
        "layers": layers,
        "merged": merged,
        "app_files": len(app_files),
        "app_top_level": dict(Counter(p.split("/")[1] for p in app_files)),
        "app_extensions": dict(Counter(PurePosixPath(p).suffix or PurePosixPath(p).name for p in app_files)),
        "app_sequences_by_file": {p: dict(c) for p, c in sorted(findings.app_sequences.items())},
        "sequence_counts": dict(findings.sequence_counts),
        "structurally_valid_sequences_outside_app": {
            area: {"files": len(paths), "sequences": sum(paths.values()),
                   "files_listed": sorted(paths)[:60]} for area, paths in findings.valid_outside_app.items()},
        "image_or_pdf_files": {"total": len(findings.images),
                               "by_area": dict(Counter(i["area"] for i in findings.images)),
                               "by_kind": dict(Counter(str(i["kind"]) for i in findings.images)),
                               "list": findings.images},
        "fonts": findings.fonts,
        "failures": failures,
        "pass": not failures,
    }
    (out / "scan_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    shown = {k: v for k, v in summary.items() if k not in ("image_or_pdf_files", "fonts")}
    shown["image_or_pdf_files"] = {k: v for k, v in summary["image_or_pdf_files"].items() if k != "list"}
    shown["fonts"] = len(summary["fonts"])
    shown["failures"] = failures[:40]
    print(json.dumps(shown, indent=1, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
