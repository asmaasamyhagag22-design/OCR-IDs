"""Create the frozen identity-aware nested Phase 2I-B development split.

The private manifest contains pseudonymous card IDs and subset labels only.  It
does not persist identity components, source paths, labels, OCR, or truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "artifacts" / "phase2f" / "split_manifest.jsonl"
OUTPUT = ROOT / "artifacts" / "phase2i_b" / "nested_development_split.jsonl"
SUMMARY = ROOT / "configs" / "phase2i_b_nested_split_v1.json"
TAG = "phase2i-b-nested-development-v1"
SOURCE_SHA256 = "cc19d7d419a3e028cf8030e7557a656399d73ede300d4f127e3b90ccba578a1c"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def assign(identity_component: str) -> tuple[str, int]:
    digest = hashlib.sha256(f"{TAG}:{identity_component}".encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 5
    return ("internal_validation" if bucket in (0, 1) else "calibration"), bucket


def build(source: Path = SOURCE) -> tuple[list[dict], dict]:
    if _sha256(source) != SOURCE_SHA256:
        raise ValueError("frozen Phase 2F split manifest hash mismatch")
    source_rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()
                   if line.strip()]
    development = [row for row in source_rows if row.get("split") == "development"]
    if len(development) != 1575:
        raise ValueError("expected exactly 1575 development cards")

    component_subsets: dict[str, set[str]] = defaultdict(set)
    private_rows = []
    buckets = Counter()
    for row in development:
        subset, bucket = assign(row["identity_component"])
        component_subsets[row["identity_component"]].add(subset)
        buckets[bucket] += 1
        private_rows.append({
            "schema_version": "phase2i_b_nested_split_card_v1",
            "card_id": row["card_id"],
            "subset": subset,
        })
    if any(len(values) != 1 for values in component_subsets.values()):
        raise AssertionError("identity component leakage across nested subsets")
    private_rows.sort(key=lambda row: row["card_id"])
    logical_hash = hashlib.sha256(
        "\n".join(_canonical(row) for row in private_rows).encode("ascii")
    ).hexdigest()
    subset_cards = Counter(row["subset"] for row in private_rows)
    subset_components = Counter()
    for component, values in component_subsets.items():
        subset_components[next(iter(values))] += 1
    summary = {
        "schema_version": "phase2i_b_nested_split_summary_v1",
        "algorithm": "sha256(tag + ':' + identity_component), first 32 bits modulo 5",
        "tag": TAG,
        "source_sha256": SOURCE_SHA256,
        "logical_manifest_sha256": logical_hash,
        "development_cards": len(private_rows),
        "development_identity_components": len(component_subsets),
        "subset_cards": dict(sorted(subset_cards.items())),
        "subset_identity_components": dict(sorted(subset_components.items())),
        "bucket_cards": {str(key): buckets[key] for key in range(5)},
        "identity_components_persisted": False,
        "internal_validation_is_external": False,
    }
    return private_rows, summary


def write(source: Path = SOURCE, output: Path = OUTPUT, summary_path: Path = SUMMARY) -> dict:
    rows, summary = build(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(_canonical(row) + "\n" for row in rows), encoding="ascii")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="ascii")
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--summary", type=Path, default=SUMMARY)
    args = parser.parse_args(argv)
    print(json.dumps(write(args.source, args.output, args.summary), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
