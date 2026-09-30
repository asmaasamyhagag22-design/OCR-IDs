# Phase 4-I - integrating BC-final with a second model

**`INTEGRATION_NOT_WORTH_IT:oracle_adds_at_most_one_card_over_bc_final`**

On the 168 internal controls, read end to end from the control photos, the
second model is the only one right on **one** card. The either-right ORACLE is
160, against BC-final's 159. The rule declared before any number (D-008,
`configs/phase4_integration_rule_v1.json` amendment 4) stops the integration at
a gain of one card or less.

As a result, steps 2 to 5 did not run: there is no fusion rule, no routing
choice and no integrated audit re-freeze. Under audit amendment 7g A, nothing
fused is sealed, and the locked read stays BC-final alone as frozen. The audit
has not been opened.

Branch `phase4-integration`. Rule: `configs/phase4_integration_rule_v1.json`
(amendments 1-4), committed before each number it governs. Decisions D-005 to
D-012 in `docs/release/decision_log.md`.

---

## The result

```
$ bash wsl_step1.sh capture && bash wsl_step1.sh read && bash wsl_step1.sh score
```

| 168 controls, end to end, crop K2 | cards |
|---|---|
| BC-final exact | **159** (Clopper-Pearson 90.1 to 97.5%) |
| Second model exact | **91** (46.3 to 61.9%) |
| Both right | 90 |
| Only BC-final right | 69 |
| Only the second model right | **1** |
| Both wrong | 8 |
| ORACLE, either right (not an accuracy) | **160** |
| ORACLE minus BC-final | **1**, so the stop fires |

- All 168 control photos were admitted, and there are 168 distinct identity
  components. The intervals treat cards as independent.
- **The sensitivity check gives the same counts.** With BC-final read from the
  pre-materialized bands instead of end to end, every cell of the table holds
  the same count.
  - **Card by card, the readings agree on 167 of 168.** This is a descriptive
    check computed afterwards from the step-1 artifacts; the check itself reads
    no label. It was run with
    `python scripts/phase4_step1.py band-agreement`, which printed
    `{"cards": 168, "bc_final_end_to_end_reading_equals_band_reading": 167}`.
  - Together with the two published totals (both 159), it follows that the one
    card that differs is wrong both ways, with two different wrong readings: a
    single disagreement cannot be offset.
  - This concerns these 168 cards only. It does not lift amendment 7f N2's
    caveat that the control bands are not the live pipeline.
- **Contamination is not why the stop fired.**
  - The second model's training data are unverified, and a string in its bundle
    matched an official labelled card (D-008). Contamination, if present, would
    be expected to inflate its results on ministry cards, which works *against*
    the stop. The stop fired anyway.
  - The measured gain is one card on these 168. What a model with verified clean
    training data would gain is not known.
  - The stop is a declared rule about this sample, not a population bound. One
    card in 168 has a Clopper-Pearson upper limit of about 3.3 percent.
- **What this does not say:** that no second model could help. It says that
  this model, with its inference reconstructed as declared (crop K2, a strict
  exactly-14-ASCII-digit reading with nothing stripped), adds one card over
  BC-final on the 168.
  - Only the two declared crops were tried, and the configuration files define
    preprocessing, not a crop.
  - **On development cards, most of its failures were in output format, not
    in misread digits.** K2 is not 14 digits on 358 of 720 cards. Of the 362
    readings that were 14 digits, 348 are exact.
  - So the result may reflect the reconstruction as much as the model. For it
    to help, it had to read correct, correctly formatted digits on BC-final's 9
    errors, and it did on 1. A different crop or decoder could change that. The
    rule and the one-card gate were fixed before any number, so nothing is
    re-run on the 168.

## How it got here

### The Linux environment, and its parity with Windows (amendment 2)

Paddle is blocked on the owner's Windows machine, so the Paddle-dependent work
ran in WSL2:
- Ubuntu 26.04.1;
- Python 3.11.9 built by uv, with the system Python untouched;
- the Windows venv's exact pins, minus `tensorflow-intel` and the project's own
  editable install;
- three system libraries the owner installed (`libgomp1`, `libsm6`, `libice6`).

The environment is recorded in `reports/phase4_integration_env.json`. Before any
Linux number was used, Linux had to reproduce the Windows results:

| check | result |
|---|---|
| BC-final on the 168 control bands | 2352/2352 slots with the same argmax, 168/168 on the same side of 0.966875, max probability difference **0.0** |
| Full pipeline on the 20 rehearsal photos | every sealed field identical on 20/20, margin difference **0.0**, both self-tests reproduced |

Three Windows-only paths were translated for the measurement processes only,
and no sealed file was touched:
- the manifests' `C:\` band paths;
- the self-test fonts that the sealed v2 config names as `C:/Windows/Fonts/...`;
- git's ownership check on the vendored detector, trusted for that one
  repository through process-scope config, with no global setting changed.

Record: `reports/phase4_integration_parity_linux.json`.

**The competition package will need attention.** The sealed v2 self-test needs
Windows fonts, which a Docker image does not have. The owner has moved this to
the service session.

### The second model needs a newer Paddle (D-012, amendment 3)

The bundle is a PIR v4 program that uses the op `linear_v2`. The project's
paddlepaddle 3.3.0 cannot build it: every load path fails in the kernel pass. It
loads under 3.3.1. It therefore ran in its own worker venv, and the project venv
stayed at 3.3.0. A shipped integrated system would have carried two Paddle
versions.

### Its inference was reconstructed from its configuration files

No author code, crop definition or licence file exists. From `inference.json`
and `inference.yml`, never the README:
- recognition only: PP-LCNetV3, SVTR and a CTC head;
- input 3 x 48 x W, BGR;
- 13 output classes: blank, `/`, ten Arabic-Indic digits, space.

The framework is Apache-2.0. `src/extraction/second_model.py` holds the declared
preprocessing and greedy CTC.

### Step 0 - the crop, chosen on development images only

These are 720 admitted, labelled development cards, with the labels read only
after both crops' readings were written:

| crop | exact | not 14 digits |
|---|---|---|
| K1, our BC-final band | **0** / 720 | 716 |
| K2, Paddle's own detection of the ID line | **348** / 720 (44.6 to 52.1%) | 358 |

K2 was chosen. It needs the canonical card the pipeline chose. That card came
from a pass-through wrapper, which was verified first: with the wrapper in
place, all 20 rehearsal photos gave sealed fields identical to the Windows seal.
664 of the 771 pool cards are replay-training cards for BC-final. That does not
bias a crop choice for the second model.

## Governance

- **Sets not touched:** the audit 377 was not opened. The consumed 618 and 321
  were not touched, and the spent 647 was not used for anything.
- **Labels read:** only the Phase 2L-A manifest targets of development cards
  (step 0), and the controls' manifest targets (step 1).
- **Outputs:** card-level outputs stay under the ignored
  `artifacts/phase4_integration/`. This report and the committed JSON are
  aggregate only.
- **History rewrites, on the owner's instruction:**
  - the second model's attribution was removed (D-008, D-009);
  - the Claude co-author trailer was removed (D-010);
  - Codex Local was replaced by the owner's identity (D-011).

  What each rewrite changed:
  - the attribution rewrite (D-009) changed file text and file names, and left
    dates unchanged;
  - D-010 changed commit messages, removing only the trailer line, and left
    every tree and date unchanged;
  - D-011 changed only the name and email fields, leaving every tree, message
    byte and date unchanged.

  The hashes are resolved by `docs/release/commit_hash_map_2026-09-27.md`.
- **Rule reviews:** the stats-reviewer read the rule, audit amendment 7g and
  amendment 1 before they were committed. It took five passes to reach PASS on
  the rule and 7g, then one PASS on amendment 1. Amendments 2 to 4 (parity,
  step 0, step 1) are reviewed together with this report.

## What this phase does not establish

- Any clean estimate. The 168 controls have been read as a gate 13+ times, and
  the second model's numbers are possibly contaminated.
- That the second model is useless on other data. It shows only that, with its
  inference as reconstructed (crop K2, a strict 14-digit reading), it does not
  complement BC-final on these controls.
- Anything about the audit, which is still unopened.

## What is left

- **The locked read is still blocked.** Its manifest pins a commit that the
  history rewrites replaced, and code has changed since. Regenerating the
  manifest for BC-final alone touches the audit's configuration, so the owner
  decides it.
- **The owner reports the result to the supervisor**, with this evidence.
