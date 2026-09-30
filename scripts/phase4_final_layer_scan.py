"""Phase 4 final pass, Stage 5: verify only the layers that changed (rule `stage_5`).

    python scripts/phase4_final_layer_scan.py --save NEW.tar --reference-diff-ids OLD.json \
        --base-save BASE.tar --head-tree DIR --out DIR

1. Layer identity. The new image's diff_ids are compared with the reference image's
   (ce10649's, from `docker image inspect`). The layers up to and including the model
   layer's check (every layer the reference and the new image share by position) must be
   identical; the report names the first position where they differ, and every layer
   from there on is "changed".
2. The changed layers only, each read from `docker save` as it is:
   - every file through the validated scanner's per-file check
     (scripts/phase4_service_image_scan.Findings.inspect: images, PDFs, fonts, and every
     14-digit sequence in all its encodings; a structurally valid one in /app fails);
   - provenance: every file under app/ must be byte-equal to the same path in the
     committed tree at HEAD (--head-tree, a `git archive HEAD` extraction); every file
     outside app/ must be byte-equal to the base image's file at the same path, or be one
     of the account files `useradd` writes (etc/passwd, group, shadow, gshadow, subuid,
     subgid and their "-" backups, var/log/lastlog, var/log/faillog), or a copy of a base
     /etc/skel file into the new home directory; a pip or root cache fails.
Only paths, counts and kinds are written; never a digit sequence.
"""
from __future__ import annotations

import argparse
import json
import sys
import tarfile
from collections import Counter
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import phase4_service_image_scan as scan  # noqa: E402

ACCOUNT_FILES = frozenset(f"etc/{name}{suffix}" for name in ("passwd", "group", "shadow", "gshadow", "subuid", "subgid")
                          for suffix in ("", "-")) | {"var/log/lastlog", "var/log/faillog"}
HOME = "home/ocr/"


def diff_ids_of(save_tar: str) -> tuple[list[str], list[str]]:
    """(diff_ids from the image config, the layer member paths in the same order) of a `docker save`."""
    with tarfile.open(save_tar, mode="r:*") as outer:
        manifest = json.loads(outer.extractfile("manifest.json").read())
        if len(manifest) != 1:
            raise SystemExit("LAYER_SCAN_BLOCKED:the save holds more than one image")
        config = json.loads(outer.extractfile(manifest[0]["Config"]).read())
    diff_ids = list(config["rootfs"]["diff_ids"])
    paths = list(manifest[0]["Layers"])
    if len(paths) != len(diff_ids):
        raise SystemExit("LAYER_SCAN_BLOCKED:the manifest and the config list different layer counts")
    return diff_ids, paths


def config_problems(save_tar: str) -> list[str]:
    """The validated scanner's config and history check (amendment 5 W4) on the new image's config."""
    with tarfile.open(save_tar, mode="r:*") as outer:
        manifest = json.loads(outer.extractfile("manifest.json").read())
        config = json.loads(outer.extractfile(manifest[0]["Config"]).read())
    return scan.config_failures(config)


def layers_of(save_tar: str, wanted: dict[str, str]) -> dict[str, bytes]:
    """{diff_id: layer tar bytes} for the wanted {diff_id: member path}, each checked against its diff_id."""
    import gzip
    import hashlib

    blobs = {}
    with tarfile.open(save_tar, mode="r:*") as outer:
        for diff_id, path in wanted.items():
            blob = outer.extractfile(path).read()
            if blob[:2] == b"\x1f\x8b":                       # a gzip blob: its diff_id is the uncompressed tar's
                blob = gzip.decompress(blob)
            if "sha256:" + hashlib.sha256(blob).hexdigest() != diff_id:
                raise SystemExit(f"LAYER_SCAN_BLOCKED:a layer does not hash to its diff_id: {path}")
            blobs[diff_id] = blob
    return blobs


def compare_ids(new: list[str], reference: list[str]) -> dict:
    first_diff = next((i for i, (a, b) in enumerate(zip(new, reference)) if a != b), min(len(new), len(reference)))
    return {"new_layers": len(new), "reference_layers": len(reference), "identical_prefix": first_diff,
            "changed_positions": list(range(first_diff, len(new)))}


def scan_layers(blobs: dict[str, bytes], changed: list[str], head_tree: Path, base: dict[str, str],
                pinned: dict[str, str]) -> dict:
    import io

    findings = scan.Findings(pinned)
    counts, provenance = Counter(), []
    skel = {PurePosixPath(p).name: d for p, d in base.items() if p.startswith("etc/skel/")}
    for diff_id in changed:
        if diff_id not in blobs:
            findings.failures.append(f"a changed layer is missing from the save: {diff_id[:19]}")
            continue
        with tarfile.open(fileobj=io.BytesIO(blobs[diff_id]), mode="r:") as layer:
            for entry in layer:
                path = entry.name.removeprefix("./")
                if PurePosixPath(path).name.startswith(".wh."):
                    counts["whiteouts"] += 1
                    continue
                if "/.cache/pip" in "/" + path or path.startswith("root/.cache"):
                    findings.failures.append(f"a pip or root cache file: {path}")
                if entry.islnk():                          # a hard link: the target's bytes, at this path
                    try:
                        data = layer.extractfile(entry).read()
                    except KeyError:
                        provenance.append(f"a hard link whose target is not in the layer: {path}")
                        continue
                    counts["hard_links"] += 1
                elif entry.isfile():
                    data = layer.extractfile(entry).read()
                else:
                    if path.startswith("app/") and not entry.isdir():
                        provenance.append(f"a link or special file in /app, not a committed file: {path}")
                    continue
                counts["files"] += 1
                area = scan.area_of(path)
                held = findings.inspect(path, data, area, "changed_layer")
                digest = scan.sha256(data)
                if path.startswith("app/"):
                    committed = head_tree / path[len("app/"):]
                    if committed.is_file() and committed.read_bytes() == data:
                        counts["app_equal_to_head"] += 1
                    else:
                        provenance.append(f"not the committed file at HEAD: {path}")
                elif base.get(path) == digest:
                    counts["equal_to_base"] += 1
                elif path in ACCOUNT_FILES and not (held["image"] or held["font"] or held["valid_sequences"]):
                    counts["account_files"] += 1
                elif path.startswith(HOME) and skel.get(PurePosixPath(path).name) == digest:
                    counts["skel_copies"] += 1
                else:
                    provenance.append(f"a file outside /app that is not the base's, an account file or a skel copy: "
                                      f"{path}")
    counts["provenance_failures"] = len(provenance)
    findings.failures.extend(provenance[:60])
    return {"counts": dict(counts), "sequence_counts": dict(findings.sequence_counts),
            "images": len(findings.images), "fonts": len(findings.fonts), "failures": findings.failures}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", required=True)
    parser.add_argument("--reference-diff-ids", required=True, help="a JSON list: the reference image's RootFS.Layers")
    parser.add_argument("--base-save", required=True, help="`docker export` of the pinned base (its rootfs)")
    parser.add_argument("--head-tree", required=True)
    parser.add_argument("--model-manifest", default=str(ROOT / "docker" / "model_files.sha256"))
    parser.add_argument("--min-identical", type=int, required=True,
                        help="how many leading layers must be identical (the base, apt, pip and model layers)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    new_ids, paths = diff_ids_of(args.save)
    reference = json.loads(Path(args.reference_diff_ids).read_text(encoding="utf-8"))
    identity = compare_ids(new_ids, reference)
    base = dict(scan.base_hashes(args.base_save))
    if "manifest.json" in base or not any(p.startswith("etc/") for p in base):
        raise SystemExit("LAYER_SCAN_BLOCKED:--base-save is not a rootfs export (docker export) of the base")
    changed = [new_ids[i] for i in identity["changed_positions"]]
    blobs = layers_of(args.save, {new_ids[i]: paths[i] for i in identity["changed_positions"]})
    result = scan_layers(blobs, changed, Path(args.head_tree), base, scan.load_manifest(args.model_manifest))
    failures = list(result.pop("failures")) + config_problems(args.save)
    if identity["identical_prefix"] < args.min_identical:
        failures.insert(0, f"only {identity['identical_prefix']} leading layers are identical to the reference; "
                           f"{args.min_identical} are required")
    summary = {"layer_identity": identity, **result, "failures": failures[:100], "failure_count": len(failures),
               "verdict": "PASS" if not failures else "FAIL"}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "layer_scan_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n",
                                                 encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "failures"}, indent=1, sort_keys=True))
    for failure in failures[:20]:
        print("FAIL:", failure)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
