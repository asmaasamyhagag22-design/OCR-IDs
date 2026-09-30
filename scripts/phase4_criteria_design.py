"""Phase 4 amendment 7e: choose criterion 4, and show the old and new false-fail.

Implements exactly the procedure declared in configs/phase4_audit_rule_v1.json
`amendment_7e_part_A`, committed before this file existed. It reads no audit
content. Its only card-level input is the 168 internal controls, used for the
typo-coincidence rate; everything else is the published control cell counts.

    python scripts/phase4_criteria_design.py typo-q     the typo coincidence rate
    python scripts/phase4_criteria_design.py design     the tables and the choice

Writes aggregate numbers only, to reports/phase4_criteria_design.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import phase2m_c as mc  # noqa: E402
from scripts import phase2m_h as h  # noqa: E402
from src.governance import guard  # noqa: E402

OUT = ROOT / "reports" / "phase4_criteria_design.json"
Q_CACHE = ROOT / "artifacts" / "phase4_audit" / "typo_q.json"

N_CARDS = 377
SEED = 20260923
SIMS = 200_000
ALPHA_ONE_SIDED = 0.05
Z_CRIT = 1.6448536269514722          # one-sided 0.05
DELTA_STAR = 0.03
POWER_DELTAS = (0.02, 0.03, 0.04, 0.05)
CAPS = tuple(range(3, 13))
NI_MARGINS = (0.02, 0.03, 0.04, 0.05)
ADMISSION = (0.10, 0.0, 0.20)          # primary first
FALSE_MISMATCH_BOUND = 0.10
TYPO_SILENT_TOLERATED = 1
RETAKE_BOUND = 0.70

# The 168 internal controls (2M-H, published): both right, BC only, v2 only, both wrong.
CONTROL_CELLS = np.array([151, 8, 1, 8], dtype=float)
CONTROL_D_WRONG = (7, 9)             # BC-final errors routed to retake at 0.966875


# --------------------------------------------------------------------------- the score test

def ni_score_z(b, c, n, delta_ni):
    """Tango-form score statistic for H0: acc_BC - acc_v2 = -delta_ni, paired.

    b = cards only BC-final reads exactly, c = cards only v2 reads exactly.
    The constrained MLE of p01 (v2-only) solves A p^2 + B p + C = 0 with
    A = 2n, B = -(b+c) + d0 (2n - b + c), C = -c d0 (1 - d0), d0 = -delta_ni.
    Large Z is evidence that BC-final is NOT worse by delta_ni or more.
    """
    b = np.asarray(b, dtype=float)
    c = np.asarray(c, dtype=float)
    n = np.asarray(n, dtype=float)
    d0 = -float(delta_ni)
    A = 2.0 * n
    B = -(b + c) + d0 * (2.0 * n - b + c)
    C = -c * d0 * (1.0 - d0)
    p01 = (-B + np.sqrt(np.maximum(B * B - 4.0 * A * C, 0.0))) / (2.0 * A)
    var = 2.0 * p01 + d0 - d0 * d0
    return (b - c - n * d0) / np.sqrt(n * np.maximum(var, 1e-300))


def ni_passes(b, c, n, delta_ni, z_crit=Z_CRIT):
    return ni_score_z(b, c, n, delta_ni) > z_crit


# --------------------------------------------------------------------------- the typo rate

def typo_q_from_controls(draws: int = 20_000) -> dict:
    """P(an admitted BC-final error is silently accepted with a structurally valid typo).

    For each control card BC-final reads wrong, the frozen generator draws
    `draws` typos of the true ID; a silent acceptance is typed == reading and
    typed structurally valid. Nothing is printed per card.
    """
    for array in (h.PRIVATE / "controls_prob_plain.npy", h.PRIVATE / "controls_labels.npy"):
        guard.check_derivative_read(array)
    pred = np.load(h.PRIVATE / "controls_prob_plain.npy").argmax(-1)
    labels = np.load(h.PRIVATE / "controls_labels.npy")
    wrong = np.flatnonzero(~(pred == labels).all(-1))
    assert len(wrong) == int(CONTROL_CELLS[2] + CONTROL_CELLS[3]), "q must come from the published 9 errors"
    per_error = []
    for k, index in enumerate(wrong):
        truth = np.repeat(labels[index][None], draws, axis=0)
        typed = h.typo_population(truth, SEED + k)["typed"]
        hit = (typed == pred[index]).all(-1)
        valid = [mc.structure_valid("".join(str(int(d)) for d in row)) for row in typed[hit]]
        per_error.append(float(np.sum(valid)) / draws)
    return {"errors": int(len(wrong)), "draws_per_error": draws,
            "q_mean": float(np.mean(per_error)) if per_error else 0.0,
            "q_max": float(np.max(per_error)) if per_error else 0.0,
            "errors_with_nonzero_q": int(sum(q > 0 for q in per_error))}


# --------------------------------------------------------------------------- the simulation

def simulate(cells_sampler, admission: float, q: float, d_wrong_sampler, sims: int, rng) -> dict:
    """Simulated audits. Returns per-simulation counts; nothing is decided here."""
    pi = cells_sampler(sims, rng)                            # (sims, 4)
    n_adm = rng.binomial(N_CARDS, 1.0 - admission, size=sims)
    counts = rng.multinomial(n_adm, pi)                      # both right, BC only, v2 only, both wrong
    b, c = counts[:, 1], counts[:, 2]
    bc_wrong = counts[:, 2] + counts[:, 3]
    silent = rng.binomial(bc_wrong, q)
    routed = rng.binomial(bc_wrong, d_wrong_sampler(sims, rng))
    return {"n_adm": n_adm, "b": b, "c": c, "bc_wrong": bc_wrong, "silent": silent, "routed": routed}


def c1_fails(s):
    return s["bc_wrong"] / np.maximum(s["n_adm"], 1) > FALSE_MISMATCH_BOUND


def c2_fails(s):
    return s["silent"] > TYPO_SILENT_TOLERATED


def old_fails(s):
    """The criteria as of 7d: C1, C2 (count, 7b B1), retake share >= 0.70 (vacuous
    when no admitted error, 7 correction A), net >= 0 and at most 3 regressed."""
    share_fail = (s["bc_wrong"] > 0) & (s["routed"] / np.maximum(s["bc_wrong"], 1) < RETAKE_BOUND)
    v2_fail = ((s["b"] - s["c"]) < 0) | (s["c"] > 3)
    return c1_fails(s) | c2_fails(s) | share_fail | v2_fail


def c4_fails(s, design):
    kind, value = design
    if kind == "cap":
        return s["c"] > value
    return ~ni_passes(s["b"], s["c"], N_CARDS, value)


def fixed(p):
    p = np.asarray(p, dtype=float) / np.sum(p)
    return lambda sims, rng: np.broadcast_to(p, (sims, 4))


def dirichlet(counts):
    return lambda sims, rng: rng.dirichlet(np.asarray(counts, dtype=float) + 0.5, size=sims)


def beta_d_wrong(k, n):
    return lambda sims, rng: rng.beta(k + 0.5, n - k + 0.5, size=sims)


def point_d_wrong(k, n):
    return lambda sims, rng: np.full(sims, k / n)


def worse_cells(delta: float, churn: str, admission: float) -> np.ndarray:
    """Admitted-card cells: v2 as on the controls (152/168 exact); BC-final lower by
    delta over ALL audit cards. Non-admitted cards are both-wrong, so the gap on
    admitted cards must be delta / (1 - admission) (instrument-validator, 7e)."""
    v2_err = 16 / 168
    bc_only = 1 / 168 if churn == "low" else 8 / 168
    both_wrong = v2_err - bc_only
    v2_only = bc_only + delta / (1.0 - admission)
    both_right = 1.0 - bc_only - v2_only - both_wrong
    return np.array([both_right, bc_only, v2_only, both_wrong])


def mc_se(p: float, n: int) -> float:
    return float(np.sqrt(max(p * (1 - p), 0.0) / n))


def rate(mask) -> float:
    return float(np.mean(mask))


def design_tables(q: float, sims: int = SIMS, seed: int = SEED) -> dict:
    designs = [("cap", c) for c in CAPS] + [("ni", d) for d in NI_MARGINS]
    name = lambda d: f"cap_{d[1]}" if d[0] == "cap" else f"ni_{d[1]:.2f}"  # noqa: E731
    scenarios = {}
    for a in ADMISSION:
        scenarios[f"S1_plug_in_admission_{a:.2f}"] = (fixed(CONTROL_CELLS), a, point_d_wrong(*CONTROL_D_WRONG))
    scenarios["S2_predictive_admission_0.10"] = (dirichlet(CONTROL_CELLS), 0.10, beta_d_wrong(*CONTROL_D_WRONG))
    scenarios["S3_equal_admission_0.10"] = (fixed([151, 4.5, 4.5, 8]), 0.10, point_d_wrong(*CONTROL_D_WRONG))

    table = {name(d): {} for d in designs}
    components, old = {}, {}
    for label, (sampler, a, dw) in scenarios.items():
        rng = np.random.default_rng(seed)
        s = simulate(sampler, a, q, dw, sims, rng)
        f12 = c1_fails(s) | c2_fails(s)
        components[label] = {"c1": rate(c1_fails(s)), "c2": rate(c2_fails(s))}
        o = rate(old_fails(s))
        old[label] = {"false_fail": o, "mc_se": mc_se(o, sims)}
        for d in designs:
            f4 = c4_fails(s, d)
            overall = rate(f12 | f4)
            table[name(d)][label] = {"overall_false_fail": overall, "mc_se": mc_se(overall, sims),
                                     "c4_alone": rate(f4)}

    power = {name(d): {} for d in designs}
    for delta in POWER_DELTAS:
        for churn in ("low", "high"):
            rng = np.random.default_rng(seed)
            s = simulate(fixed(worse_cells(delta, churn, 0.10)), 0.10, q,
                         point_d_wrong(*CONTROL_D_WRONG), sims, rng)
            for d in designs:
                power[name(d)].setdefault(f"{delta:.2f}", {})[churn] = rate(c4_fails(s, d))
    for d in designs:
        for delta, both in power[name(d)].items():
            both["min"] = min(both["low"], both["high"])

    constrained = [lab for lab in scenarios if lab.startswith(("S1", "S2"))]
    eligible = []
    for d in designs:
        n = name(d)
        ff_ok = all(table[n][lab]["overall_false_fail"] <= 0.05 for lab in constrained)
        pw = power[n][f"{DELTA_STAR:.2f}"]["min"]
        if ff_ok and pw >= 0.80:
            eligible.append((pw, -table[n]["S2_predictive_admission_0.10"]["overall_false_fail"],
                             1 if d[0] == "ni" else 0, n))
    chosen, tie = None, None
    if eligible:
        ranked = sorted(eligible, reverse=True)
        if len(ranked) > 1 and ranked[0][:3] == ranked[1][:3]:
            tie = [e[3] for e in ranked if e[:3] == ranked[0][:3]]   # undeclared: stop, do not pick
        else:
            chosen = ranked[0][3]
    return {"scenarios": list(scenarios), "constrained_scenarios": constrained,
            "old_criteria_false_fail": old, "components_c1_c2": components,
            "new_false_fail": table, "power_c4_fails_when_worse": power,
            "eligible": [e[3] for e in sorted(eligible, reverse=True)],
            "chosen": chosen, "undeclared_tie": tie, "sims": sims, "seed": seed, "n_cards": N_CARDS}


def cmd_typo_q(args) -> None:
    result = typo_q_from_controls(args.draws)
    Q_CACHE.parent.mkdir(parents=True, exist_ok=True)
    Q_CACHE.write_text(json.dumps(result, indent=2) + "\n", encoding="ascii")
    print(json.dumps(result, indent=2))


def cmd_design(args) -> None:
    q = json.loads(Q_CACHE.read_text(encoding="ascii"))
    result = {"schema_version": "phase4_criteria_design_v1",
              "declared_in": "configs/phase4_audit_rule_v1.json amendment_7e_part_A",
              "typo_q": q, "delta_star": DELTA_STAR, **design_tables(q["q_mean"], args.sims)}
    OUT.write_text(json.dumps(h.me.to_py(result), indent=2, sort_keys=True) + "\n", encoding="utf-8",
                   newline="\n")
    print(json.dumps({"chosen": result["chosen"], "eligible": result["eligible"]}, indent=2))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    q = sub.add_parser("typo-q")
    q.add_argument("--draws", type=int, default=20_000)
    q.set_defaults(func=cmd_typo_q)
    d = sub.add_parser("design")
    d.add_argument("--sims", type=int, default=SIMS)
    d.set_defaults(func=cmd_design)
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
