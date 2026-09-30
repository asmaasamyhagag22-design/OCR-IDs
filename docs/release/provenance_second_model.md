# Provenance - the contributed digit recognition model

Decisions: `docs/release/decision_log.md` D-006, D-008.

## Origin

The second model; the author waived attribution.

## Permission and licence

- **Permission.** The weights are used under the author's written permission.
  The owner keeps that message privately; it is not in this repository.
- **Framework licence: Apache-2.0.** The bundle's own non-README files show the
  model was produced with PaddlePaddle / PaddleOCR:
  - `inference.json` is a PaddlePaddle PIR program (`"magic": "pir"`,
    version 4), with a `MultiHead/CTCHead`;
  - `inference.yml` is a PaddleOCR/PaddleX inference config (`RecResizeImg`,
    `CTCLabelDecode`, an `Hpi` backend section).

  The installed framework packages declare Apache-2.0: paddlepaddle 3.3.0,
  paddleocr 3.3.2, paddlex 3.3.13 (`pip show`). The bundle itself carries no
  licence text.

## What the model is

Reconstructed from `inference.json` and `inference.yml` only. No author code
exists.

- Recognition only. The architecture is a PP-LCNetV3 backbone, an SVTR neck and
  a CTC head (the PP-OCRv4/v5 mobile recognition family). There is no detector
  in the bundle.
- Input: float32, N x 3 x 48 x W, BGR. The config resizes to 3 x 48 x 320.
- Output: per time step, a softmax over 13 classes: the CTC blank, `/`, the ten
  Arabic-Indic digits, and space. The output length is not fixed to 14.
- There is no normalization inside the graph. Preprocessing is the framework's
  standard `RecResizeImg`.
- The crop it was trained on is unknown. Candidate crops are declared in
  `configs/phase4_integration_rule_v1.json` and chosen on development images.

## The files

Copied from the gitignored bundle `paddleocr_fixed_crop_bundle/` in this
worktree to the ignored tree `artifacts/second_model/rec_digits_finetuned/`.
Never committed to git.

| File | Bytes | sha256 |
|---|---|---|
| `inference.json` | 165,163 | `b315c53bb574b8229288c784b0519558b29e608f808490546316c2ca4914e2f6` |
| `inference.pdiparams` | 8,894,976 | `185b6080fb37b35037185b1567508bbc049a92abd2246a95124971338fa1a917` |
| `inference.yml` | 743 | `797da0591093b0745d0f1db98c54e74980a1e403e0d98dfc5953072543a7f33e` |

**The bundle README is excluded.** It is treated as containing a real national
ID. It is never opened or copied, and nothing from it enters the repository. A
count-only scan found no 14-digit run in `inference.json` or `inference.yml`
(the binary `.pdiparams` holds weights only).

## Training data

**Synthetic per the author; unverified; the author is unreachable.**

- The model directory is named `rec_digits_finetuned`, so the model was
  fine-tuned.
- A string in the bundle README matched a card in the official labelled set.
  The model may therefore have seen ministry cards.

**Every ministry-data number for this model, or for the fused system, is marked
"possibly contaminated".** That covers the 168 controls and the audit. The
fused system's audit numbers are descriptive and cannot be certified
(D-008; `configs/phase4_audit_rule_v1.json` amendment 7g).
