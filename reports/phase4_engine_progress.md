# Phase 4 engine progress

2026-10-06. **ENGINE_READY**. **GO for Asmaa's push**. Final outgoing scan and
ref verification passed. No push performed. Work branch codex/phase4-engine;
backup backup/phase4-engine-entry-20261006. Prepared delivery ref:
codex/engine-delivery, one normal-author commit atop depi/ocr-team/national-id
(76f6ed5). Read the ref directly for its final commit hash.

Owner rule amendment: D-088 records the owner's team confirmation of downstream
comparison of every success with the applicant's typed ID. Engine v2 default
ENGINE_SUCCESS_MIN_MARGIN=0.0; invalid reading -> INVALID_READING; confidence
always present. Positive margin restores LOW_CONFIDENCE gate for valid readings.
No-reading/timeout policy retained. v1 and 0.966875 unchanged. Rule amendments
1/2 were committed before proof; README_SERVICE documents the assumption.

Checks: WSL fast target 228 passed; ruff, format, strict mypy, vulture PASS;
frozen checks PASS, no sealed/model/pipeline/governance changes. Stage 7 already
STAGE7_READY. Rebuilt once for this amendment, from f3811cf:
fixed14-ocr-service:fixed14-bcfinal-3101-f3811cf
sha256:9c78e3b694ddce665553834060dd01a3cae16083d815360a329638c2fee1cdc5
Changed-layer scan PASS: 16 identical prefix; positions 16/17 changed; 67 files,
56 app files equal source, zero failures. Parity rerun PASS: 296/296 identical
in all eleven fields; all honest 200, zero mismatches. No full image rescan.
Shipping source still equals the rebuilt commit; no second rebuild.

Exact proof: same first 100 permitted S_dev in declared development_pool order,
governed before staging in neutral index/Nid_Front/Nid_Front.jpg; byte-preserving
copy. Mongo field download folder path uses <IP_adress>. API client in Docker,
Mongo/nginx in Docker, exact source except allowed configuration constants,
--dry-run. Passive observation checks response order, coverage and confidence.
No concurrent task workloads. Cold API warm-up 18.302 s. Status: 99 success,
one INVALID_READING; 98/100 successful-and-correct, 98/100 correct regardless
of status, one wrong-but-success. Client batch p50 7.898 s, p95 23.054 s,
max 29.571 s, none reaching 60 s; zero 503s/request/processing timeouts.

Exact function proof: national-id2 fed4461 isolated export, fresh throwaway
Python 3.11.9 venv, CORNER_DEVICE=cpu OCR_DEVICE=cpu. All 26 source/model blobs
verified unchanged before/after. Same 100 images and --dry-run. Engine/API
client stopped. Status: 75 success, 25 ocr_error; 74/100 successful-and-correct,
86/100 correct regardless, one wrong-but-success. Twelve correct readings have
NEEDS_MANUAL_REVIEW. Client batch p50 36.228 s, p95 53.038 s, max 58.369 s,
none reaching 60 s. Both clients left all Mongo output fields unchanged.

Sweep declared before proof, descriptive only (correct success / wrong success):
0.0: 98/1; 0.9: 92/1; 0.966875: 86/0; 0.99: 70/0. All denominators 100.
No further inference, threshold tuning, audit/spent access, human review or
label changes. Development results are not held-out accuracy.

Capture (D-090): LOG_FILE=/dev/null, externally launch exact scripts with pipes;
hash readings in memory and redact reading fields/all long digit sequences
before saving. No raw readings or labels persisted/displayed. Instrument
validation covers metrics, parser, redaction, sweep, quantiles and ordering.
Ignored proof evidence: artifacts/phase4_engine/exact/*_summary.json,
configuration.json, national_id2_source_verification.json, redacted logs,
neutral hashes and setup-failure records. Never commit private artifacts.

Initial function attempt failed Mongo connectivity after model loading but
before images; Docker internal network did not activate host ports. D-091
connected only Mongo/nginx to the bridge too, activating loopback ports;
verified media 200 and 100 untouched documents before the sole completed
function measurement. A shell redirect quoting error also preceded a launch;
saved scripts corrected it. No data/API rerun or second rebuild. All proof
containers/internal network and both anonymous Mongo volumes removed;
private export/venv and image retained.

Final report: reports/phase4_engine.md, including Waiting for Asmaa with exact
push/Docker commands. D-083..D-093 document autonomous decisions. Final outgoing
scan PASS: 317 files, zero errors, no unapproved IDs, secrets, tracked data,
new weights or files >10 MiB. Pinned model/license blobs are inherited. Ref
ancestry, direct parent, one-commit count and normal author/committer verified;
shipping build inputs equal f3811cf. Asmaa pushes herself:

git push depi codex/engine-delivery:ocr-team/national-id
