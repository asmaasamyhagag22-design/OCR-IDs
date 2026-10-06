# Phase 4 batch engine delivery

2026-10-06. Decision: **ENGINE_READY**. **GO for Asmaa's push**.
Final outgoing-tree scan and ref verification passed. No push performed.

## Owner rule amendment

D-088 records the owner's request in this chat, citing the team's confirmation
that downstream compares every engine success reading with the applicant's
typed national ID. Wrong readings therefore fail that comparison. This is
owner-provided confirmation; the downstream application was not independently
inspected. Append-only amendments 1/2 in configs/phase4_engine_rule_v1.json
were committed before the exact proof.

Engine decision_rule_version is phase4-engine-v2. Every structurally valid
completed reading has finding and empty error_codes at any margin by default.
Structurally invalid readings retain finding with exactly INVALID_READING.
No reading retains exactly one declared error; timeout preserves any best
checkpoint with PROCESSING_TIMEOUT. Confidence remains in every item, null
for unknown/nonfinite margins. ENGINE_SUCCESS_MIN_MARGIN defaults to 0.0;
finite values in 0..1 are accepted. Positive values enable LOW_CONFIDENCE for
valid readings below the configured margin or without a finite margin.
README_SERVICE.md documents the downstream comparison assumption.

Only /engine policy changed. The /v1 decider and 0.966875, recognizer,
decoder, F1v, F8, F9, BC-final, sealed modules, model files and governance
source remain unchanged. Back input is accepted and ignored. The strict
1..10-item envelope, 25 MiB item/350 MiB total caps, two persistent spawn
workers/four native threads, single-batch admission, 45-second shared item
budget, 49-second steady response bound and 130-second initial warm-up
allowance are retained. Per-image errors stay in HTTP 200; malformed payloads
get 422, total oversize 413, busy/recovery 503. Engine auth is optional;
v1 authentication and startup key requirement remain unchanged.

## Rebuild and verification

- Stage 7 was already STAGE7_READY (D-081). Current WSL fast target:
  **228 passed**, 1070 deselected. Ruff, format, strict mypy and vulture pass.
  Tests cover default success at any margin, invalid readings, margin settings,
  positive-boundary behavior through a real spawned worker, confidence,
  timeout precedence, existing v1 behavior and the prior engine protocol.
- Frozen verification: all_pass true; v1 primary margin 0.966875; sealed edits
  empty; checkpoint and recognizer pins match. Windows fonts are supplied via
  the documented WSL test-only Pillow search path.
- One rebuild for this amendment, from f3811cf:
  fixed14-ocr-service:fixed14-bcfinal-3101-f3811cf
  image ID sha256:9c78e3b694ddce665553834060dd01a3cae16083d815360a329638c2fee1cdc5.
- Changed-layer scan **PASS**: sixteen identical prefix layers; zero-based
  positions 16/17 changed; 67 files, all 56 app files equal committed source;
  zero failures, images or fonts. Dependency/model layers reused; no full
  image rescan. Shipping source remained equal to f3811cf afterward.
- Rebuilt-container parity rerun **296/296 equal in all eleven fields**,
  zero mismatches, all honest responses 200. Selection: 168 controls,
  143 fix-touched and four previously oversized files, with overlap.
- Earlier worker-memory evidence remains applicable: combined RSS
  2,086,064,128 bytes for two loaded workers, with the additional v1 runtime.
  Recommend 6 GiB and 512 pids, one uvicorn worker.

Commands: bash scripts/lint_shipped.sh; XDG_DATA_HOME=/mnt/c/Windows
PYTHON=$HOME/venvs/ocrid-service-py3119/bin/python bash scripts/test_service.sh;
python scripts/phase4_final.py check; bash scripts/phase4_service_docker.sh
build; bash scripts/phase4_final_image.sh layer-scan IMAGE; python
scripts/phase4_final.py container-subset --url http://127.0.0.1:8081 --run
enginev2. All model execution was Linux/WSL, never native Windows Paddle.

## Exact-client proof

Both supplied scripts ran as scripts with --dry-run. Source differences were
verified as configuration-only (MONGO_URI, OCR_ENGINE_URL for API, MEDIA_SERVER_IP,
LOG_FILE). The original placeholder, batch size, workers, retries, timeouts,
OCR and classification code were retained. Original file SHA256:

- API: f88460cd0e011a32740f1f2c335bda21ed9c65b36f04bf04de5e0c3245d2758e
- Function: e454674952e4008d08d42e30490d0b4c8ae56bdf2a188160891cedc606321550

The same first 100 permitted S_dev in development_pool order were used for
both clients, selected before measurement. Existing Access/governance gates
preceded image reads. Bytes were copied unchanged under neutral index/
Nid_Front/Nid_Front.jpg; Mongo download folder path used literal <IP_adress>.
MongoDB and static nginx ran in Docker on an internal proof network, with
loopback-only host ports. The API client and passive HTTP observer also ran
in Docker. During API proof only these required proof services ran as task
workloads; tests, scanners, parity and other inference were stopped. Necessary
OS, Docker Desktop and Codex background services remained. Before function
proof the engine and API client were stopped; only the exact CPU function
client used model compute, with Mongo/nginx still providing its inputs.

The API client used the rebuilt image after a restart, exercising its own
cold warm-up. The function client used national-id2 fed4461 in a fresh
throwaway Python 3.11.9 venv, CORNER_DEVICE=cpu and OCR_DEVICE=cpu set before
import. All 26 exported Git source/model blobs were verified unchanged.
Dependencies: torch 2.14.1+cpu, ultralytics 8.4.173, paddlepaddle 3.3.1,
paddleocr 3.7.0; imported OpenCV 4.10.0. Pinned dependencies were cloned from
the previous isolated environment; pymongo was added. No national-id2 code,
model, Git ref or remote changes. Its outputs were not adapted to our policy.

The first function attempt loaded the model but failed Mongo connectivity
before enumeration, downloads or image OCR; its empty records and redacted
setup log are retained separately. The internal network did not activate host
port mappings. D-091 attached only Mongo/nginx to the default bridge as well,
activating their loopback-only ports. Media HTTP 200 and 100 pending documents
were verified before the single completed function measurement. No client,
data, policy, pipeline or image changes; no API rerun. An inline shell quoting
error also occurred before launching a client and was corrected using a saved
shell script. Neither setup failure contributes a measurement denominator.

D-090 configures LOG_FILE=/dev/null. External subprocess pipe capture hashes
readings in memory and redacts complete IDs and reading fields before saving
logs. No raw readings or labels were saved/displayed or committed. No client
function was imported, monkeypatched or replaced. Scores use exact reading
hash equality with only those development labels. The instrument's single
pre-proof validation cycle covered synthetic accuracy denominators, status
parsing, redaction, threshold boundaries, interpolated quantiles and order
rejection. Actual API response order, coverage, confidence and client statuses
matched observation for all 100 items. Both dry runs left Mongo unchanged.

| Metric, all 100 images | Exact API client | Exact function client |
|---|---:|---:|
| Client status counts | 99 success; 1 ocr_error | 75 success; 25 ocr_error |
| Success and correct | 98/100 | 74/100 |
| Correct regardless of status | 98/100 | 86/100 |
| Wrong but success | 1 | 1 |
| Client batch latency p50 | 7.898 s | 36.228 s |
| Client batch latency p95 | 23.054 s | 53.038 s |
| Client batch latency max | 29.571 s | 58.369 s |
| Client batches reaching 60 s | 0/10 | 0/10 |

Engine errors: one `INVALID_READING`. Function errors: 12
`NEEDS_MANUAL_REVIEW`, seven `FIT_TOO_POOR`, two `NOT_AN_EGYPTIAN_ID`,
four `ID_NOT_14_DIGITS`. All twelve review-flagged readings were correct.

Client latency includes downloads, OCR and result logging; warm-up is excluded.
Quantiles use linear interpolation over ten batches. API engine HTTP round trips:
p50 7.673 s, p95 22.791 s,
max 29.387 s; none reached 60 seconds. Cold engine warm-up
HTTP call: 18.302 s (within 180 seconds). HTTP 503 responses:
0; client request timeouts: 0;
PROCESSING_TIMEOUT items: 0.

The function script's success requires a plausible reading and its existing
confident/crop-agreement flag. Correct readings with review errors count in
the second metric but not the first. These are descriptive development results,
not held-out accuracy or a reconstruction of a historical 78% run. Every image,
including failed/no-reading items, remains in both denominators.

## Declared descriptive threshold sweep

Computed from the exact API responses, holding structural and processing-error
gates fixed. No extra inference, selection or shipped-threshold change.
ENGINE_SUCCESS_MIN_MARGIN remains 0.0; v1 remains 0.966875.

| Hypothetical minimum margin | Success and correct, all images | Wrong but success |
|---|---:|---:|
| 0.0 | 98/100 | 1 |
| 0.9 | 92/100 | 1 |
| 0.966875 | 86/100 | 0 |
| 0.99 | 70/100 | 0 |

## Outgoing tree and Waiting for Asmaa

Final scan **PASS** before creating the delivery ref: no unapproved national
IDs, secrets, tracked data, new weights or files over 10 MiB. Only unchanged,
hash-pinned, already-published model/license blobs are inherited. Approved
synthetic and inherited test fixtures retain their provenance-scoped exceptions.
No private label set is read by that scan. README preserves 76f6ed5's intent:
usage and links, with no status/accuracy figures.

codex/engine-delivery contains exactly one new commit atop the freshly fetched
depi/ocr-team/national-id tip, using the owner's normal configured identity
asmaasamyhagag22-design <asmaasamyhagag22-design@users.noreply.github.com>.
No push occurred. Proof services and both anonymous Mongo volumes are removed;
private redacted evidence
and the verified image are retained. Asmaa can push without force:

```bash
git push depi codex/engine-delivery:ocr-team/national-id
```

The local verified tag has not been published. The team builds after fetching
with bash scripts/phase4_service_docker.sh build and substitutes the printed
image tag. Supply a private ocr.env as README_SERVICE.md explains, then run:

```bash
docker run -d --name fixed14-engine --restart unless-stopped -p 127.0.0.1:8081:8081 --env-file ocr.env --read-only --tmpfs /tmp --tmpfs /home/ocr:uid=10001,gid=10001 --cap-drop ALL --security-opt no-new-privileges --ulimit core=0 --memory 6g --pids-limit 512 fixed14-ocr-service:fixed14-bcfinal-3101-f3811cf
```

Default success assumes the downstream typed-ID comparison. Bind localhost or
an isolated internal interface. Raise ENGINE_SUCCESS_MIN_MARGIN if that comparison
stops; it changes only engine policy. D-083 through D-093
entries record every autonomous choice: baseline/ref preservation, inherited
Stage 7, bounded process pool/checkpoints, budgets and caps, resource sizing,
key/network policy, unchanged pipeline, WSL execution/fonts, prior partial
proof and disclosed setup failures, privacy-aware scan, normal-author one-commit
release, owner amendment, fixed selection/sweep, exact Docker clients, throwaway
CPU national-id2 export/venv, config-only verification, external capture and
redaction, passive observations, isolation, final scanning/ref and cleanup.

Earlier ENGINE_PARTIAL:2 and adapter-only figures predated the supplied clients
and amendment. They remain historical in the decision log; the exact proof
above is the current release evidence. No audit/spent images or labels used.
