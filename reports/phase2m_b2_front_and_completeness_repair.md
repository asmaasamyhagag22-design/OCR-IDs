# Phase 2M-B2 — Front-side safety and digit-line completeness repair

## Decision

`FRONT_EVIDENCE_UNDERPOWERED`

The phase was closed by owner instruction before any implementation, training,
threshold choice, or evaluation. No fallback was implemented or enabled.
Production preprocessing, the primary route, and the Phase 2L-D demo are
unchanged. The rejected Phase 2M-B candidate and its report are preserved
unmodified.

## Why it is underpowered

The repaired candidate's front-evidence gate requires at least 100 real
wrong-side / not-an-ID negatives. Authorized development data does not contain them:

| Required safety evidence | Minimum | Available in authorized data |
|---|---:|---|
| Wrong-side / not-an-ID negatives | 100 | 6 (1 `WRONG_SIDE`, 5 `NOT_AN_ID`, calibration only) |
| Egyptian-ID backs | part of the above | 0 |
| Non-card negatives | 100 | 37 real local images |
| Genuine-front positives | 200 | available |
| Synthetic clipped-line negatives | 300 | constructible from external-train digit boxes |

The remaining reviewed wrong-side / not-an-ID cards belong to the consumed
internal-validation split and were not opened. The external archive has zero
back-side images. Each internal card has exactly one image, so there is no
unlabeled back-side population. Geometric transformations of fronts cannot
faithfully create card backs. No owner-authorized negatives were supplied; the
owner chose to close the phase instead of waiting.

## Entry-state verification

| Check | Result |
|---|---|
| Branch / base | `phase2m-b2-preprocessing-repair` at `ac2f0ea` |
| Backup of rejected candidate | `backup/phase2m-b1-rejected-20260915` → `ac2f0ea` |
| Worktree clean at entry | yes |
| Fixed14-v2 config SHA-256 | `93a783f022f025f54903ebeafe6293c4dfe432dd222208923daf3b2202103267` |
| Fixed14-v2 weights SHA-256 (64 hex) | `d8c6614dd4fcb02ecf1690145aa6ed5bdc15be6c9984f9516ba9078111e3da1b` |
| 168-card reconstruction record | intact |
| Validated internal result | 576/618 = 93.20%, unchanged |
| Phase 2M-B rule, config, sources, locator weights | all match frozen hashes |
| External validation (699) opened | 0 |
| External test (321) opened | 0 |
| Consumed internal validation / locked audit accessed | 0 / 0 |

`EXTERNAL_VALIDATION_OPENED = 0`

## Carried-forward conclusions

- The direct-field fallback remains research-only and disabled.
- `challenge_A` remains a capture/admission failure. The correct user-facing
  action is to request a clearer, complete image.
- `challenge_B` is correctly registered; its error is a recognition-domain
  defect for Fixed14-v3.
- The primary full-card detector admits some non-card and wrong-side inputs.
  This is disclosed, not changed.

## Exact next action

Start Phase 2M-C (Fixed14-v3 recognition improvement) from this closed state,
without the fallback.

`FRONT_EVIDENCE_UNDERPOWERED`
