"""What is running, for /version, the responses and rollback checks."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache

MODEL_VERSION = "fixed14-bcfinal-3101"
#: The decision path. route() (scripts/phase2m_h.route, bcfinal_only) and the threshold are unchanged;
#: v2 names the whole path the decisions come from, now with F9 (phase 4 final, bumped on owner instruction).
DECISION_RULE_VERSION = "phase4-routing-v2"
#: The pipeline around the frozen models: decode by content, F1v, F8 (D-036), F9 (phase 4 final, amendment 2).
PIPELINE_VERSION = "phase4-final-f1v-f8-f9-v2"
THRESHOLD_KEY = "bcfinal_only_margin_threshold"


@lru_cache(maxsize=1)
def margin_threshold() -> float:
    """The frozen primary margin, read from the sealed candidate config, never restated."""
    from src.extraction import fixed14_bcfinal_shadow as shadow

    return float(shadow.frozen_margins()[shadow.PRIMARY_MARGIN_KEY])


def thresholds_document(threshold: float | None = None) -> str:
    """The canonical JSON whose sha256 /version reports (D-037)."""
    value = margin_threshold() if threshold is None else float(threshold)
    return json.dumps({THRESHOLD_KEY: value}, sort_keys=True, separators=(",", ":"))


def thresholds_sha256(threshold: float | None = None) -> str:
    """The SHA-256 of the thresholds document that /version reports."""
    return hashlib.sha256(thresholds_document(threshold).encode("ascii")).hexdigest()


def version_info() -> dict[str, str]:
    """The /version body."""
    return {
        "model_version": MODEL_VERSION,
        "decision_rule_version": DECISION_RULE_VERSION,
        "thresholds_sha256": thresholds_sha256(),
        "pipeline_version": PIPELINE_VERSION,
    }
