"""Drive the Phase 2C analysis over a set of upright canonical cards.

The inputs are a Phase 1.1A audit manifest (which card, which side, how many
quarter turns), optionally the Phase 1 geometry predictions, and optionally an
operator-supplied front/back pairing.

The pairing is deliberately an *input*, never an inference. Deciding that two
photographs are two sides of one physical card is a claim about the world that
this pipeline has no way to establish; asserting it would let a wrong guess
create a false ``national_id_front_vs_back`` failure against a real applicant.
Without a pairing that check reports ``not_available`` and says why.
"""

import json
from pathlib import Path

import numpy as np
from PIL import Image

from .cross_checks import CHECK_ID_SIDES, FAIL, PASS
from .document import analyse
from .field_states import NEEDS_REVIEW
from .regions import load_policy, load_region_config

CANONICAL_NAME = "upright_canonical.png"


def load_manifest(path):
    """Read a Phase 1.1A audit manifest into ``[{id, side, quarter_turns, image}]``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    root = Path(path).parent
    configured_output = data.get("output_directory")
    if configured_output:
        configured_output = Path(configured_output)
        if not configured_output.is_absolute():
            configured_output = Path(__file__).resolve().parents[2] / configured_output
    cards = []
    for record in data.get("records", []):
        image = record.get("upright_canonical")
        if not image and configured_output:
            image = str(configured_output / f"index_{record['index']}" / CANONICAL_NAME)
        if not image:
            image = str(root / f"index_{record['index']}" / CANONICAL_NAME)
        cards.append(
            {
                "id": str(record.get("index") or record.get("id")),
                "side": record.get("side", "front"),
                "quarter_turns": int(record.get("quarter_turns_ccw", record.get("quarter_turns", 0))),
                "image": str(image),
                "source_original": record.get("source"),
            }
        )
    return cards


def load_geometry(path):
    """Index Phase 1 predictions by the basename of the original photograph."""
    if not path:
        return {}
    indexed = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        prediction = payload.get("prediction") or {}
        indexed[Path(payload.get("input", "")).name] = {
            "document_score": prediction.get("document_score"),
            "accepted": prediction.get("accepted", payload.get("status") == "accepted"),
            "rejection_reason": prediction.get("rejection_reason"),
            "inference_ms": prediction.get("inference_time_ms"),
            "source": str(path),
            "model": payload.get("model"),
        }
    return indexed


def load_pairs(path):
    """Read an operator-asserted front/back pairing, symmetrised."""
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    pairs = data.get("pairs", data) if isinstance(data, dict) else {}
    table = {}
    for left, right in pairs.items():
        table[str(left)] = str(right)
        table.setdefault(str(right), str(left))
    return table


def _id_field(record):
    return record.fields.get("national_id_number")


def _read_id_digits(record):
    """The national ID digits a finished record settled on, or ''."""
    field = _id_field(record)
    return field.value if field and field.value else ""


def _as_read_id_digits(record):
    """The digits the engine itself read, before any repair in this run.

    This is the only form of a counterpart value that may be used as
    cross-record evidence. A repaired value is not evidence: it is this run's
    own output, and feeding it back to the other side of the same card is what
    lets two records rewrite each other into agreement.
    """
    field = _id_field(record)
    if field is None:
        return ""
    if field.repair_applied and field.repair_evidence:
        return field.repair_evidence.get("original_digits") or ""
    return field.value or ""


def _id_is_structurally_valid(record):
    """Whether this record's own ID reading passed every structural rule."""
    field = _id_field(record)
    if field is None or not field.value:
        return False
    return not any(str(n).startswith("structural_problem_") for n in field.notes)


def _pairing_disclosure(card_id, record, partner_id, partner):
    """Everything a human needs to settle a front/back disagreement themselves.

    Both values, both confidences, both crops and both untouched engine reads,
    on both records, whether or not the two agree.
    """

    def side(cid, rec):
        field = _id_field(rec)
        return {
            "card_id": cid,
            "layout": rec.layout,
            "image": (rec.meta or {}).get("input_path"),
            "value_as_read": _as_read_id_digits(rec),
            "value_final": _read_id_digits(rec),
            "repair_applied": bool(field.repair_applied) if field else False,
            "confidence": field.confidence if field else None,
            "raw_ocr_text": (field.raw_ocr or {}).get("text") if field and field.raw_ocr else None,
            "region_name": field.region_name if field else None,
            "crop_bbox_in_rectified": list(field.bbox_in_rectified) if field and field.bbox_in_rectified else None,
            "structurally_valid": _id_is_structurally_valid(rec),
        }

    this, other = side(card_id, record), side(partner_id, partner)
    agree = bool(this["value_as_read"]) and this["value_as_read"] == other["value_as_read"]
    return {
        "counterpart_card_id": partner_id,
        "evidence_provenance": "counterpart_value_as_read_never_repaired",
        "readings_agree": agree,
        "this_side": this,
        "counterpart_side": other,
        "review_note": (
            "the two sides of this physical card were read as different numbers; "
            "neither reading was altered, both are shown above with their crops "
            "and confidences, and a human must decide which is correct"
            if not agree
            else "both sides of this physical card were read as the same number"
        ),
    }


class PairedRecordDivergence(AssertionError):
    """Two paired records finished with national IDs that cannot both be right.

    Raised only for an *incoherent* result, never for an honest disagreement.
    A front and a back that were read differently, both kept unchanged, both
    reporting ``national_id_front_vs_back`` as ``fail`` and both marked for
    review, is the correct output of this pipeline. What must never happen is
    two paired records ending on different numbers while reporting that the
    two sides agree - that means something rewrote a value and then checked it
    against the thing it was rewritten to match.
    """


def reconcile(records, pairs):
    """Third pass: disclose every pairing and assert the pair is coherent.

    Attaches ``meta['pairing']`` to both records of every pair - both values,
    both crops, both confidences - and returns the list of card-id pairs whose
    two sides disagree, so a caller can report them.
    """
    seen, diverged = set(), []
    for card_id, partner_id in pairs.items():
        key = tuple(sorted((str(card_id), str(partner_id))))
        if key in seen:
            continue
        seen.add(key)
        left, right = records.get(key[0]), records.get(key[1])
        if left is None or right is None:
            continue

        left.meta["pairing"] = _pairing_disclosure(key[0], left, key[1], right)
        right.meta["pairing"] = _pairing_disclosure(key[1], right, key[0], left)

        left_final, right_final = _read_id_digits(left), _read_id_digits(right)
        if left_final and left_final == right_final:
            # The two sides ended on the same number. One of them may still
            # report ``fail``, because the check compared against the other's
            # *as-read* digits and a same-image repair changed that side
            # afterwards. That stale failure is left standing on purpose:
            # re-running the check against the repaired value would be exactly
            # the mutable-evidence loop this pass exists to prevent. It is
            # conservative - it can only send a card to review - so it is
            # labelled rather than cleared.
            for record, other_key in ((left, key[1]), (right, key[0])):
                if record.cross_checks.get(CHECK_ID_SIDES, {}).get("result") == FAIL:
                    record.notes.append(
                        f"front_back_check_failed_against_card_{other_key}_as_read_digits "
                        f"which_a_same_image_repair_later_changed; both_sides_now_hold_the "
                        f"same_number_and_this_failure_is_not_re_evaluated_against_a_"
                        f"repaired_value"
                    )
            continue

        # The two sides ended on different numbers. That is allowed only when
        # both records say so plainly - or when the check could not run at all.
        diverged.append(key)
        for record, other_key in ((left, key[1]), (right, key[0])):
            check = record.cross_checks.get(CHECK_ID_SIDES, {})
            result = check.get("result")
            field = _id_field(record)

            if result == PASS:
                # The defect signature. Two sides holding different numbers
                # while reporting that they match means a value was rewritten
                # and then checked against the thing it was rewritten to match.
                raise PairedRecordDivergence(
                    f"cards {key[0]} and {key[1]} finished with different national IDs "
                    f"({left_final!r} vs {right_final!r}) but card "
                    f"{record.meta.get('card_id')} reports {CHECK_ID_SIDES}={result!r}. "
                    f"A disagreement reported as agreement means a value was rewritten "
                    f"and then checked against what it was rewritten to match."
                )

            if result != FAIL:
                # The check never ran - typically one side produced fewer than
                # 14 digits, so there was nothing to compare. Two unreadable
                # fragments differing is not a contradiction, and the reason is
                # already on the check. Record it and move on.
                record.notes.append(
                    f"paired_card_{other_key}_holds_a_different_national_id_but_"
                    f"{CHECK_ID_SIDES}_was_{result}_so_the_two_were_never_compared"
                )
                continue

            if field is not None and field.state != NEEDS_REVIEW:
                raise PairedRecordDivergence(
                    f"cards {key[0]} and {key[1]} disagree on the national ID but card "
                    f"{record.meta.get('card_id')} left national_id_number in state "
                    f"{field.state!r}; an unresolved front/back disagreement must need "
                    f"review"
                )
            if field is not None and field.repair_applied:
                raise PairedRecordDivergence(
                    f"card {record.meta.get('card_id')} repaired its national ID while "
                    f"still disagreeing with card {other_key}; a front/back disagreement "
                    f"must never drive a repair"
                )
            record.notes.append(f"front_back_disagreement_with_card_{other_key}_neither_reading_altered")
            if "unresolved_front_back_national_id_disagreement" not in record.problems:
                record.problems.append("unresolved_front_back_national_id_disagreement")
    return diverged


def run(
    manifest,
    engine,
    output_dir,
    geometry_path=None,
    pairs_path=None,
    region_config=None,
    policy=None,
    today=None,
    id_line_detector=None,
    enable_id_line_localizer=True,
):
    """Analyse every card in ``manifest``; return ``(records, paths)``.

    Three passes.

    1. Read every card on its own, with no counterpart. Nothing here can be
       influenced by another image.
    2. Re-run the cards that have a counterpart, supplying the partner's
       **as-read** digits from pass 1 - never the partner's repaired value.
       Cross-record evidence has to be read-only, or two records repair
       towards each other and end up agreeing on a number neither engine
       read. Re-running keeps every record the single output of one
       ``analyse`` call rather than a finished record patched after the fact.
    3. Reconcile each pair and assert the result is coherent: either both
       sides finished with the same digits, or both report
       ``national_id_front_vs_back`` as ``fail`` and both need review. Two
       paired records that finished with different numbers while claiming the
       check passed is a defect in this pipeline, not a result, and raises.
    """
    config = region_config if isinstance(region_config, dict) else load_region_config(region_config)
    policy = policy if isinstance(policy, dict) else load_policy(policy)
    cards = load_manifest(manifest) if not isinstance(manifest, list) else manifest
    geometry = load_geometry(geometry_path)
    pairs = load_pairs(pairs_path)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    warmup = getattr(engine, "warmup", None)
    if callable(warmup):
        warmup()
    line_config = None
    if (
        enable_id_line_localizer
        and getattr(engine, "name", None) == "paddleocr"
        and any(card["side"] == "front" for card in cards)
    ):
        from .hybrid import PaddleRoiDetector
        from .id_line_localizer import load_localizer_config

        if id_line_detector is None:
            root = Path(__file__).resolve().parents[2]
            id_line_detector = PaddleRoiDetector(cache_dir=root / "artifacts" / "paddlex_cache")
        detector_warmup = getattr(id_line_detector, "warmup", None)
        if callable(detector_warmup):
            detector_warmup()
        line_config = load_localizer_config()

    def analyse_card(card, **counterpart):
        image = np.array(Image.open(card["image"]).convert("RGB"))
        original = Path(card.get("source_original") or card["image"]).name
        return analyse(
            image,
            engine,
            source_image=card["image"],
            region_config=config,
            policy=policy,
            layout=card["side"],
            today=today,
            geometry=geometry.get(original),
            quarter_turns=card["quarter_turns"],
            model_versions=(geometry.get(original) or {}).get("model"),
            id_line_detector=id_line_detector,
            id_line_config=line_config,
            **counterpart,
        )

    # Pass 1: every card alone. No cross-record evidence exists yet, so nothing
    # read here can have been shaped by another image.
    first = {}
    for card in cards:
        first[card["id"]] = analyse_card(card)

    # Pass 2: re-run paired cards against the partner's as-read digits.
    records, paths = {}, {}
    for card in cards:
        partner = pairs.get(card["id"])
        if partner and partner in first:
            other = first[partner]
            other_field = _id_field(other)
            record = analyse_card(
                card,
                counterpart_id=_as_read_id_digits(other),
                counterpart_source=f"card {partner} ({other.layout}), paired by operator-supplied manifest",
                counterpart_supplied=True,
                counterpart_as_read=True,
                counterpart_structurally_valid=_id_is_structurally_valid(other),
                counterpart_confidence=other_field.confidence if other_field else None,
            )
            record.notes.append(f"counterpart_card_{partner}_supplied_by_pairing_manifest")
            record.notes.append("counterpart_value_used_was_as_read_never_repaired")
        else:
            record = first[card["id"]]
            if partner:
                record.notes.append(f"pairing_named_card_{partner}_which_was_not_in_this_run")
        record.meta["card_id"] = card["id"]
        record.meta["pairing_source"] = str(pairs_path) if pairs_path else None
        records[card["id"]] = record

    # Pass 3: reconcile, disclose, and assert coherence. This mutates the
    # records, so nothing may be written to disk before it has run.
    reconcile(records, pairs)

    for card in cards:
        record = records[card["id"]]
        destination = output_dir / f"card_{card['id']}"
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / "result.json"
        target.write_text(json.dumps(record.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        paths[card["id"]] = {"result": str(target), "image": card["image"], "directory": str(destination)}

    summary = output_dir / "results.jsonl"
    with summary.open("w", encoding="utf-8") as handle:
        for card_id, record in records.items():
            payload = record.to_dict()
            payload["card_id"] = card_id
            payload["image"] = paths[card_id]["image"]
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    return records, paths
