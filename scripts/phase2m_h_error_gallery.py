"""A folder of the images BC-final reads wrong, for looking at.

Two populations, the two this phase measured:
    external_validation/   the 19 wrong records of the 647 (spent selection set)
    controls_168/          the 9 wrong records of the 168 internal controls

For each wrong record, two images: the 160x576 band the model actually read, and
the original photograph it was cut from. Nothing is predicted anew; the readings
are the ones Phase 2M-G and 2M-H already materialized.

Everything lands under artifacts/phase2m_h/error_gallery/, which git ignores -
card-level material lives nowhere else (a project rule). No file name and no line of
the index carries a national ID or any digit of one: only the card's hash id,
the weakest-position margin, the positions of the wrong slots, and which message
the release design would show at the frozen primary threshold. The images
themselves show the card, which is why they stay in the ignored tree.

External reads go through Access and are persisted to the ledger. Every file
copied is checked by the runtime guard first.
"""
from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import phase2m_c as mc  # noqa: E402
from scripts import phase2m_e as me  # noqa: E402
from scripts import phase2m_h as h  # noqa: E402
from src.governance import guard  # noqa: E402

OUT = h.PRIVATE / "error_gallery"
IMAGE_ROOTS = (ROOT / "data" / "official_eval" / "images",
               ROOT.parent / "OCR_ID_working" / "data" / "official_eval" / "images")


def primary_threshold() -> float:
    sealed = json.loads(h.FROZEN_CANDIDATE.read_text(encoding="utf-8"))
    return float(sealed["operating_point"]["primary"]["margin"])


def wrong_slots(pred: np.ndarray, truth: np.ndarray) -> list[int]:
    """1-based positions, never the digits."""
    return [int(i) + 1 for i in np.flatnonzero(np.asarray(pred) != np.asarray(truth))]


def message(margin: float, threshold: float) -> str:
    """What an honest user sees: their reading is wrong, so it is a mismatch."""
    return "retake_the_photo" if margin < threshold else "check_the_number_you_typed"


def save_band(band: np.ndarray, target: Path) -> None:
    rgb = np.asarray(band, dtype=np.uint8)
    cv2.imwrite(str(target), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))


def find_original(card_id: str) -> Path | None:
    for root in IMAGE_ROOTS:
        for suffix in (".jpg", ".png"):
            candidate = root / f"{card_id}{suffix}"
            if candidate.is_file():
                return candidate
    return None


def external_validation(threshold: float, access: me.Access) -> list[dict]:
    out_dir = OUT / "external_validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = h.validation_frame()                       # guarded, and Access-checked
    bands, _labels = me.load_split(h.VALIDATION)
    access.record(h.VALIDATION, "materialized_bands")
    meta = me.split_meta(h.VALIDATION)
    access.record(h.VALIDATION, "meta")

    rows = []
    wrong = np.flatnonzero(~frame["correct"])
    for rank, index in enumerate(sorted(wrong, key=lambda i: frame["margin"][i]), start=1):
        margin = float(frame["margin"][index])
        slots = wrong_slots(frame["pred"][index], frame["labels"][index])
        stem = f"{rank:02d}_margin{margin:.4f}_slots-{'-'.join(map(str, slots))}"
        save_band(bands[index], out_dir / f"{stem}_band.png")

        member = meta[index]["member"]
        original = mc.load_external(member, h.VALIDATION, access)   # guard + Access inside
        cv2.imwrite(str(out_dir / f"{stem}_photo.png"), cv2.cvtColor(original, cv2.COLOR_RGB2BGR))
        rows.append({"population": "external_validation", "rank": rank,
                     "file_stem": stem,
                     # 12 characters are enough to group records by card, and cannot
                     # hold a 14-digit run - the full 64-hex hash sometimes does.
                     "card_group": meta[index]["identity_group"][:12],
                     "margin": round(margin, 6), "wrong_slot_positions": slots,
                     "n_wrong_slots": len(slots), "message_at_primary": message(margin, threshold),
                     "photo": "yes"})
    return rows


def controls(threshold: float) -> list[dict]:
    out_dir = OUT / "controls_168"
    out_dir.mkdir(parents=True, exist_ok=True)
    prob_path, label_path = h.PRIVATE / "controls_prob_plain.npy", h.PRIVATE / "controls_labels.npy"
    for array in (prob_path, label_path):
        guard.check_derivative_read(array)
    prob, labels = np.load(prob_path), np.load(label_path)
    pred, margin = prob.argmax(-1), h.weakest_margin(prob)
    _replay, _eligible, pool = mc.internal_pools()     # same order cmd_tta used
    if len(pool) != len(pred):
        raise SystemExit("PHASE2M_H_BLOCKED:control arrays do not align with the control pool")

    rows = []
    wrong = np.flatnonzero(~(pred == labels).all(-1))
    for rank, index in enumerate(sorted(wrong, key=lambda i: margin[i]), start=1):
        record = pool[index]
        slots = wrong_slots(pred[index], labels[index])
        stem = (f"{rank:02d}_margin{float(margin[index]):.4f}"
                f"_slots-{'-'.join(map(str, slots))}_{record['card_id']}")
        band = Path(record["broad_band_reference"])
        guard.check_card_read(band)
        shutil.copyfile(band, out_dir / f"{stem}_band{band.suffix}")
        original = find_original(record["card_id"])
        if original is not None:
            guard.check_card_read(original)
            shutil.copyfile(original, out_dir / f"{stem}_photo{original.suffix}")
        rows.append({"population": "controls_168", "rank": rank, "file_stem": stem,
                     "card_group": record["card_id"], "margin": round(float(margin[index]), 6),
                     "wrong_slot_positions": slots, "n_wrong_slots": len(slots),
                     "message_at_primary": message(float(margin[index]), threshold),
                     "photo": "yes" if original is not None else "not found in either worktree"})
    return rows


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    threshold = primary_threshold()
    access = me.Access()
    rows = external_validation(threshold, access) + controls(threshold)
    access.persist("phase2m_h_error_gallery")

    with (OUT / "index.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "wrong_slot_positions": " ".join(map(str, row["wrong_slot_positions"]))})
    (OUT / "README.txt").write_text(
        "Images BC-final seed 3101 reads wrong.\n"
        "Sorted by weakest-position margin, lowest first. File names carry the margin and the\n"
        "1-based positions of the wrong slots - never a digit.\n"
        f"message_at_primary is what the release design shows an honest user at T = {threshold}.\n"
        "*_band is what the model read; *_photo is the original capture.\n"
        "external_validation is a spent selection set; controls_168 is a gate read many times.\n"
        "Card-level material: keep it under artifacts/, which git ignores.\n", encoding="utf-8")

    for population in ("external_validation", "controls_168"):
        subset = [r for r in rows if r["population"] == population]
        retake = sum(r["message_at_primary"] == "retake_the_photo" for r in subset)
        print(f"{population:20s} {len(subset):3d} wrong records  "
              f"({retake} would be told to retake, {len(subset) - retake} to check their number)")
    print(f"written to {OUT.relative_to(ROOT).as_posix()}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
