"""Prepare and scan one outgoing commit without pushing or reading any private labels.

Windows git plumbing preserves the owner checkout. The release tree retains the
remote's already-published model blobs, pinned by the existing manifest.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "artifacts/phase4_engine"
BASE = "depi/ocr-team/national-id"
BRANCH = "codex/engine-delivery"


def git(*args, data=None, env=None):
    return subprocess.run(["git", "-C", str(ROOT), *args], input=data, capture_output=True,
                          check=True, env=env).stdout


def entries(ref):
    result = {}
    for entry in git("ls-tree", "-rz", ref).split(b"\0"):
        if entry:
            meta, name = entry.split(b"\t", 1)
            mode, kind, oid = meta.decode().split()
            if kind != "blob":
                raise SystemExit("RELEASE_BLOCKED:non_blob_tree_entry")
            result[name.decode()] = (mode, oid)
    return result


def blobs(oids):
    """Read exact git objects in one batch; validate boundaries without decoding their content."""
    wanted = sorted(set(oids))
    data = git("cat-file", "--batch", data=("\n".join(wanted) + "\n").encode())
    result, offset = {}, 0
    for expected in wanted:
        end = data.index(b"\n", offset)
        oid, kind, size = data[offset:end].decode().split()
        if oid != expected or kind != "blob":
            raise SystemExit("RELEASE_INSTRUMENT_FAIL:git_batch_order")
        length = int(size)
        start = end + 1
        result[oid] = data[start:start + length]
        if len(result[oid]) != length or data[start + length:start + length + 1] != b"\n":
            raise SystemExit("RELEASE_INSTRUMENT_FAIL:git_batch_boundary")
        offset = start + length + 1
    if offset != len(data):
        raise SystemExit("RELEASE_INSTRUMENT_FAIL:git_batch_trailing_content")
    return result


def candidate_tree():
    OUT.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_INDEX_FILE": str(OUT / "release.index")}
    git("read-tree", "HEAD", env=env)
    baseline = entries(BASE)
    for path, (mode, oid) in baseline.items():
        if path.startswith(("artifacts/", "third_party/")):
            git("update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}", env=env)
    return git("write-tree", env=env).decode().strip()


def fixture_scope(path):
    return path.startswith("tests/") or path == "scripts/phase4_service_image_scan.py"


def permitted_sequence(kind, pin_ok, digits, allowed, previous):
    return (kind == "inside_hex_hash" or (kind == "inside_decimal_literal" and pin_ok)
            or digits in allowed or digits in previous)


def scan(tree):
    from scripts import phase4_service_image_scan as scanner

    current = entries(tree)
    baseline = entries(BASE)
    content = blobs([oid for _, oid in current.values()] + [oid for _, oid in baseline.values()])
    allowed = {r["value"] for r in json.loads((ROOT / "configs/synthetic_id_allowlist.json").read_text())["ids"]}
    probe = next(iter(allowed))
    found = scanner.sequences("probe.txt", probe.encode(), False)
    if not any(d == probe and scanner.could_be_an_id(d) for _, _, d in found):
        raise SystemExit("RELEASE_INSTRUMENT_FAIL:positive_sequence")
    if any(d == probe and k == "inside_hex_hash" for _, k, d in found):
        raise SystemExit("RELEASE_INSTRUMENT_FAIL:hash_classification")
    checks = [permitted_sequence("other", False, probe, {probe}, set()),
              permitted_sequence("other", False, probe, set(), {probe}),
              not permitted_sequence("other", False, probe, set(), set()),
              not permitted_sequence("inside_decimal_literal", False, probe, set(), set()),
              permitted_sequence("inside_decimal_literal", True, probe, set(), set()),
              permitted_sequence("inside_hex_hash", False, probe, set(), set()),
              fixture_scope("tests/fixture.py"), not fixture_scope("src/fixture.py")]
    if not all(checks):
        raise SystemExit("RELEASE_INSTRUMENT_FAIL:fixture_filter")
    pinned = {line.split("  ", 1)[1].removeprefix("/app/"): line.split("  ", 1)[0]
              for line in (ROOT / "docker/model_files.sha256").read_text().splitlines() if line.strip()}
    errors, large, weights, synthetic, inherited_synthetic = [], [], [], 0, 0
    credential = re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|"
                            rb"AKIA[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
    changed = sum(baseline.get(p) != item for p, item in current.items())
    for path, (_, oid) in current.items():
        data = content[oid]
        size = len(data)
        inherited = current[path] == baseline.get(path)
        pin_ok = path in pinned and hashlib.sha256(data).hexdigest() == pinned[path]
        if size > 10 * 1024 * 1024:
            large.append({"path": path, "bytes": size, "inherited": inherited})
            if not inherited:
                errors.append({"path": path, "kind": "new_file_over_10_mib"})
        if path.endswith((".h5", ".pdiparams", ".pt", ".onnx")):
            weights.append({"path": path, "bytes": size, "inherited": inherited, "hash_pinned": pin_ok})
            if not inherited or not pin_ok:
                errors.append({"path": path, "kind": "new_or_unpinned_weight"})
            continue
        if path.startswith("data/") or (path.startswith("artifacts/") and not pin_ok):
            errors.append({"path": path, "kind": "tracked_data"})
        if scanner.nested_images(path, data):
            errors.append({"path": path, "kind": "image_or_pdf"})
        if credential.search(data):
            errors.append({"path": path, "kind": "secret"})
        prior_fixture_digits = set()
        if path in baseline and fixture_scope(path):
            previous = content[baseline[path][1]]
            prior_fixture_digits = {digits for _, _, digits in scanner.sequences(path, previous, False)}
        for encoding, kind, digits in scanner.sequences(path, data, pin_ok):
            if kind == "inside_hex_hash" or (kind == "inside_decimal_literal" and pin_ok):
                continue
            if not scanner.could_be_an_id(digits):
                continue
            if digits in allowed:
                synthetic += 1
            elif digits in prior_fixture_digits:
                # D-082's published tree already vetted these synthetic test/scanner fixtures.
                # This exemption is path-specific and cannot permit a newly introduced value.
                inherited_synthetic += 1
            elif not permitted_sequence(kind, pin_ok, digits, allowed, prior_fixture_digits):
                errors.append({"path": path, "kind": "unapproved_structural_id", "encoding": encoding})
    result = {"base": git("rev-parse", BASE).decode().strip(), "tree": tree,
              "files_scanned": len(current), "changed_or_new_files": changed,
              "new_weight_blobs": sum(not r["inherited"] for r in weights),
              "weights": weights, "files_over_10_mib": large, "approved_synthetic_occurrences": synthetic,
              "inherited_test_fixture_occurrences": inherited_synthetic,
              "private_labels_read": False, "instrument_validation": "PASS", "errors": errors,
              "verdict": "PASS" if not errors else "FAIL"}
    (OUT / "release_scan.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return result


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("scan", "commit"):
        raise SystemExit("usage: phase4_engine_release.py scan | commit")
    tree = candidate_tree()
    result = scan(tree)
    if result["errors"]:
        raise SystemExit("ENGINE_RELEASE_SCAN_FAIL")
    if sys.argv[1] == "commit":
        ready = "Decision: **ENGINE_READY**" in (ROOT / "reports/phase4_engine.md").read_text(encoding="utf-8")
        proof = "Exact team-client proofs completed." if ready else "Exact team-client proof remains pending."
        message = ("Add bounded batch engine and delivery verification\n\n"
                   "Preserve v1 routing and the frozen pipeline. Use the owner-approved engine success policy. "
                   + proof + "\n").encode()
        commit = git("commit-tree", tree, "-p", result["base"], data=message).decode().strip()
        git("update-ref", f"refs/heads/{BRANCH}", commit)
        print(json.dumps({"branch": BRANCH, "commit": commit,
                          "author": git("show", "-s", "--format=%an <%ae>", commit).decode().strip(),
                          "one_new_commit": git("rev-list", "--count", f"{BASE}..{BRANCH}").decode().strip() == "1"}))


if __name__ == "__main__":
    main()
