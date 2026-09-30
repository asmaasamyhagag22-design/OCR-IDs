# Research history: not part of the shipped service and never copied into its image
# (docker/app_code_files.txt, phase 4 final Stage 7).
"""Find a card's image by its stem, whatever the extension and the letter case.

The audit's resolver (scripts/phase4_audit.py resolve_audit_paths) tried exactly
`<card_id>.jpg`, then `<card_id>.png`, in lower case. That is sealed with the
audit and stays as it is. A file stored as `.JPG`, `.jpeg`, `.PNG`, `.webp`, with
an upper-case stem, or as ingest's `<card_id>_<n>` for a card's later images,
would have been reported missing. The competition package resolves images
through this module instead (D-029).

Matching is on the file name only. Nothing is opened: whether the bytes decode is
the decoder's question (fixed14_demo_adapter.decode_image sniffs content, never
the extension).
"""
from __future__ import annotations

import os
from pathlib import Path

#: Preferred first when a stem has several files; any other extension still matches.
PREFERRED_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff")


def base_name(file_name: str) -> str:
    """The part of a file name before its first dot, lower-cased ('a.JPG.bak' -> 'a')."""
    return file_name.split(".", 1)[0].lower()


def stem_index(file_name: str, stem: str) -> int | None:
    """0 for `<stem>.<anything>`, n for ingest's `<stem>_<n>.<anything>`, else None.

    Case-insensitive on both the stem and the extension. A longer stem that merely
    starts with this one ('abcd' for 'abc') does not match.
    """
    base, target = base_name(file_name), stem.lower()
    if base == target:
        return 0
    prefix = target + "_"
    if base.startswith(prefix) and base[len(prefix):].isdigit():
        return int(base[len(prefix):])
    return None


def _preference(path: Path, index: int) -> tuple:
    suffix = path.suffix.lower()
    rank = PREFERRED_SUFFIXES.index(suffix) if suffix in PREFERRED_SUFFIXES else len(PREFERRED_SUFFIXES)
    return (index, rank, path.name)


def find_images(directory: str | Path, stem: str) -> list[Path]:
    """Every file in `directory` (not recursive) matching `stem`, best first.

    Order: the stem's own file before its `_1`, `_2`...; among equals, the
    common image extensions first; then by name, so the order is stable.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []
    hits = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if not entry.is_file():
                continue
            index = stem_index(entry.name, stem)
            if index is not None:
                hits.append((Path(entry.path), index))
    return [path for path, index in sorted(hits, key=lambda hit: _preference(*hit))]


def resolve_image(directory: str | Path, stem: str) -> Path | None:
    """The best matching file, or None when there is none."""
    found = find_images(directory, stem)
    return found[0] if found else None


class ImageIndex:
    """One directory scan, then constant-time lookups: for thousands of images."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        #: lower-cased stem -> [(path, index)]; a file `x_3.jpg` is filed under both
        #: 'x_3' (index 0) and 'x' (index 3), so either lookup finds it
        self._by_stem: dict[str, list[tuple[Path, int]]] = {}
        if self.directory.is_dir():
            with os.scandir(self.directory) as entries:
                for entry in entries:
                    if not entry.is_file():
                        continue
                    path, base = Path(entry.path), base_name(entry.name)
                    self._by_stem.setdefault(base, []).append((path, 0))
                    head, sep, tail = base.rpartition("_")
                    if sep and head and tail.isdigit():
                        self._by_stem.setdefault(head, []).append((path, int(tail)))

    def find(self, stem: str) -> list[Path]:
        hits = self._by_stem.get(stem.lower(), [])
        return [path for path, index in sorted(hits, key=lambda hit: _preference(*hit))]

    def resolve(self, stem: str) -> Path | None:
        found = self.find(stem)
        return found[0] if found else None
