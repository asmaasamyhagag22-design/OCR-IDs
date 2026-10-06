"""Derive the governance blocklist from the split manifests.

Nothing here is guessed. The three forbidden sets are defined by membership
columns in three manifests, and this script resolves that membership to the
exact paths the data guard can see:

  locked audit (377)        artifacts/phase2f/split_manifest.jsonl, split=audit
  internal validation (618) artifacts/phase2i_b/nested_development_split.jsonl,
                            subset=internal_validation (636 rows; the consumed
                            618 is a subset, so the superset is blocked)
  external test (321)       artifacts/phase2m_a/external_split_manifest.jsonl,
                            split=external_test

Card images are data/official_eval/images/<card_id>.{jpg,png} - the filename is
the card_id. External images are neutral-named sha256(archive_member_reference)
plus the original suffix, matching load_external() in scripts/phase2m_c.py.

The output carries the sha256 of every source manifest. The data guard refuses
to trust a blocklist whose sources have moved, so a manifest change fails the
guard closed rather than silently narrowing it.

No value written here identifies a person: card_ids and neutral names are
hashes. The annotation targets in the external manifest are never read.

    python .claude/hooks/build_blocklist.py            # write the blocklist
    python .claude/hooks/build_blocklist.py --verify   # check it is current
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import PurePosixPath, Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "governance" / "blocklist.json"

AUDIT_MANIFEST = ROOT / "artifacts" / "phase2f" / "split_manifest.jsonl"
INTERNAL_MANIFEST = ROOT / "artifacts" / "phase2i_b" / "nested_development_split.jsonl"
EXTERNAL_MANIFEST = ROOT / "artifacts" / "phase2m_a" / "external_split_manifest.jsonl"

CARD_IMAGES = "data/official_eval/images"
EXTERNAL_IMAGES = "artifacts/phase2m_a/external_extracted_neutral"

#: Expected sizes. A manifest that stops producing these is a governance
#: incident, not a rounding difference, so the build refuses rather than
#: writing a blocklist that is quietly too small.
EXPECTED = {"audit": 377, "internal_validation": 636, "external_test": 321}

#: Structural metadata for the external test: identity group, duplicate group
#: and member path, with no labels. Phase 2M-E declared this the single
#: permitted test-related input and read it for counts only; that precedent is
#: carried forward explicitly rather than left to interpretation.
PERMITTED_DERIVATIVES = ("external_test_meta.jsonl",)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        raise SystemExit(f"BLOCKLIST_SOURCE_MISSING:{path.relative_to(ROOT).as_posix()}")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def neutral_name(member: str) -> str:
    """The on-disk name of an external image, as load_external() computes it."""
    return hashlib.sha256(member.encode("utf-8")).hexdigest() + PurePosixPath(member).suffix.lower()


def derive() -> dict:
    audit = sorted({r["card_id"] for r in read_jsonl(AUDIT_MANIFEST) if r.get("split") == "audit"})
    internal = sorted({r["card_id"] for r in read_jsonl(INTERNAL_MANIFEST)
                       if r.get("subset") == "internal_validation"})
    external = sorted({neutral_name(r["archive_member_reference"])
                       for r in read_jsonl(EXTERNAL_MANIFEST) if r.get("split") == "external_test"})

    found = {"audit": len(audit), "internal_validation": len(internal), "external_test": len(external)}
    if found != EXPECTED:
        raise SystemExit(f"BLOCKLIST_COUNTS_CHANGED:expected={EXPECTED} found={found}")

    blocked_paths = []
    image_dir = ROOT / CARD_IMAGES
    for card_id in audit + internal:
        for suffix in (".jpg", ".png"):
            if (image_dir / f"{card_id}{suffix}").is_file():
                blocked_paths.append(f"{CARD_IMAGES}/{card_id}{suffix}")

    return {
        "schema_version": "governance_blocklist_v1",
        "generated_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "sources": {
            AUDIT_MANIFEST.relative_to(ROOT).as_posix(): sha256_file(AUDIT_MANIFEST),
            INTERNAL_MANIFEST.relative_to(ROOT).as_posix(): sha256_file(INTERNAL_MANIFEST),
            EXTERNAL_MANIFEST.relative_to(ROOT).as_posix(): sha256_file(EXTERNAL_MANIFEST),
        },
        "counts": found,
        "resolved_image_paths": len(blocked_paths),
        "blocked_card_ids": {"audit": audit, "internal_validation": internal},
        "blocked_external_names": external,
        "blocked_paths": sorted(blocked_paths),
        "governed_roots": [CARD_IMAGES, EXTERNAL_IMAGES],
        "permitted_derivatives": list(PERMITTED_DERIVATIVES),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true",
                        help="check the written blocklist matches the manifests; write nothing")
    args = parser.parse_args(argv)

    fresh = derive()
    if args.verify:
        if not OUT.is_file():
            print(f"BLOCKLIST_MISSING:{OUT.relative_to(ROOT).as_posix()}")
            return 1
        current = json.loads(OUT.read_text(encoding="utf-8"))
        drift = [k for k in ("sources", "counts", "blocked_card_ids", "blocked_external_names",
                             "blocked_paths", "governed_roots")
                 if current.get(k) != fresh[k]]
        if drift:
            print("BLOCKLIST_STALE:" + ",".join(drift))
            return 1
        print(f"blocklist current: {current['counts']}, "
              f"{current['resolved_image_paths']} image paths resolved")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fresh, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT).as_posix()}: {fresh['counts']}, "
          f"{fresh['resolved_image_paths']} image paths resolved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
