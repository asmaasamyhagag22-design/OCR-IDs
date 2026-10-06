# Phase 2M-D — Local Fixed14-v3 demo

`FIXED14_V3_LOCAL_SHADOW_DEMO_READY`

A local-only Tkinter window (`scripts/phase2m_d_local_v3_shadow_demo.py`) runs
the frozen Fixed14-v3 research candidate. It reads the exact band produced by
the unchanged automatic pipeline. It is a demo only: production routing,
`main`, the existing v2 demo (`scripts/phase2l_d_local_demo.py`) and the v2
adapter are unchanged. Nothing was merged.

## Owner UI decision

The phase started as a side-by-side v2/v3 comparison. During the phase the
owner asked for a window showing **only the new model**, with minimal
wording. The shipped window therefore contains only:
- upload;
- photo preview;
- extract;
- the v3 number;
- the exact band that produced it;
- copy and reset.

A tiny `Fixed14-v3 · تجريبي` tag marks it as experimental. The v2 panel,
agreement line, timings and feedback buttons were removed from the UI.

The comparison still runs underneath, for safety and verification only. The
unchanged adapter runs detector, orientation, canonicalization, band
preparation and v2 inference once. A pass-through proxy captures the exact
160×576 tensor, and v3 reads a byte-identical copy of it. The window never
chooses between models by confidence: it always displays the v3 result.

If the card or band cannot be obtained, the window shows `RETAKE_REQUIRED ·
أعد تصوير البطاقة كاملة وبوضوح` and no number. This covers unsupported or
corrupt files, document not found, invalid corners, and perspective,
orientation or band failures.

## Startup safety

The window refuses to open (exit code 2) unless every check passes:
- Fixed14-v2 config `93a783f022f025f54903ebeafe6293c4dfe432dd222208923daf3b2202103267` and weights `d8c6614dd4fcb02ecf1690145aa6ed5bdc15be6c9984f9516ba9078111e3da1b`;
- Fixed14-v3 weights `27cbcf9f93890d85a91f63c3c1a07ed1f8a7a4e85803ad3d90b60394ffdc492a` (538,296 bytes);
- the v3 frozen-candidate config `dc968ce313a4ff685620a66b288fa8d96b461a9376dcc0b4d238b7a7ad435362` and rule `e4bdac468adf311151313650c0313935419f08627e6568c0616eb2113348d2aa` (LF-normalized);
- a deterministic synthetic self-test reproduces the stored v2 prediction hash, and only then is v3 loaded and its own stored prediction hash checked.

## Verification

| Check | Result |
|---|---|
| All v2/v3 hash checks | 10/10 pass |
| Self-tests (v2 before v3, then v3) | pass |
| v2 through the demo runtime vs its frozen 168-card record | 168/168 identical |
| v3 through the demo runtime vs its frozen control predictions | 168/168 identical |
| Byte-identical tensor to both models (owner demo photo) | yes |
| v2 through the demo equals the unchanged adapter result | yes |
| Owner's partially off-frame photo | `RETAKE_REQUIRED`, no number |
| Two fresh-process GUI sessions: upload, extract, v3 displayed, Copy, Reset clears state and clipboard | pass, pass |
| Real launch: window mapped and viewable | yes (about 16 s model load) |
| Listening ports / TCP connections of the running demo | 0 / 0 |
| New image or array files after sessions | 0 |
| Leftover files with `TEMP` redirected to an isolated folder | 0 |
| ID-shaped values in any process output | 0 |
| Focused tests `tests/test_phase2m_d_shadow_demo.py` | 12 passed |
| Full suite | 767 passed, 4 skipped |

No consumed 618, external 699, external 321 or locked 377 record was opened by
the demo or its verification. The owner's own demo photos were used only
through the runtime, and no number was printed. Aggregate verification output
is in the ignored `artifacts/phase2m_d/verification.json`.

## Launch

```powershell
cd C:\Users\asmaa\Documents\OCR_ID_parallel
.\.venv\Scripts\python.exe scripts\phase2m_d_local_v3_shadow_demo.py
```

## Limits

v3 is a research candidate. Its confirmed gain is on external digit-box
samples. On real internal photos it improved the 168 regression controls
(+6 exact) but was not validated on a clean real-photo set. The
owner-simplified UI does not show v2 for comparison. The shipped pipeline has
no separate line-completeness gate: `RETAKE_REQUIRED` covers only failures the
unchanged pipeline itself detects. Review each number before use.

`FIXED14_V3_LOCAL_SHADOW_DEMO_READY`
