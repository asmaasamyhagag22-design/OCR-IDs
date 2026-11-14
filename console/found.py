"""Front images stored outside the path MongoDB records for them.

scripts/team_download_problems.py indexes every ``<ID>/Nid_Front/<image>`` under the nginx root
and writes ``found_front_images.json`` (record id -> image path on disk) for the records whose
stored ``download folder path`` does not hold their image. scripts/team_reread_found_images.py
read those images and wrote their results; the stored path itself was never changed.

The console reads the same file, so a reviewer sees the image the OCR actually read. The disk
path is turned into a URL on the same media host (the path under the nginx root), so the console
needs no access to the disk. The file holds national IDs in its paths: nothing here logs or
returns a path, only whether a record has one.
"""
from __future__ import annotations

import json
import logging
import os
import posixpath
import threading
import urllib.parse
from pathlib import Path

log = logging.getLogger("console.found")


class FoundImages:
    def __init__(self, path: str, media_root: str) -> None:
        self.path = Path(path) if path else None
        self.root = posixpath.normpath(media_root.replace("\\", "/")) if media_root else ""
        self._signature: tuple[int, int, int] | None = None
        self._map: dict[str, str] = {}
        self._lock = threading.Lock()

    def _load(self) -> dict[str, str]:
        """The map, re-read whenever the file's mtime, size or ctime changes.

        ctime is part of the signature so that fixing a file's permissions (chmod or chown change
        ctime, not mtime) is picked up without a restart, and size so that a read that caught a
        half-written file is retried once the write completes.
        """
        if self.path is None:
            return {}
        with self._lock:
            try:
                st = self.path.stat()
            except OSError:
                self._map, self._signature = {}, None
                return self._map
            signature = (st.st_mtime_ns, st.st_size, st.st_ctime_ns)
            if signature != self._signature:
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                    self._map = {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
                except (OSError, ValueError) as exc:
                    log.warning("found-images file not usable (%s); ignored until it changes", type(exc).__name__)
                    self._map = {}
                self._signature = signature
            return self._map

    def readable(self) -> bool:
        if self.path is None:
            return False
        try:
            with self.path.open("rb"):
                return True
        except OSError:
            return False

    def __len__(self) -> int:
        return len(self._load())

    def __contains__(self, key: str) -> bool:
        return key in self._load()

    def relative(self, key: str) -> str | None:
        """The image's path under the media root, or None (unknown record, or a path outside the root)."""
        disk = self._load().get(key)
        if not disk or not self.root:
            return None
        disk = posixpath.normpath(disk.replace("\\", "/"))
        if os.path.isabs(disk) or disk.startswith("/"):
            if not (disk == self.root or disk.startswith(self.root.rstrip("/") + "/")):
                return None
            disk = disk[len(self.root.rstrip("/")) + 1:]
        if not disk or disk.startswith("../") or disk == "..":
            return None
        return disk

    def url(self, key: str, media_host: str) -> str | None:
        rel = self.relative(key)
        if rel is None:
            return None
        quoted = "/".join(urllib.parse.quote(part) for part in rel.split("/"))
        return f"http://{media_host}/{quoted}"
