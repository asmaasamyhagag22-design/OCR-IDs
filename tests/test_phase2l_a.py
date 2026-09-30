import json
from pathlib import Path

import numpy as np

from scripts import phase2l_a as p


ROOT = Path(__file__).resolve().parents[1]


def test_fixed14_config_is_calibration_only_and_truth_blind_at_runtime():
    config = json.loads((ROOT / "configs" / "phase2l_a_fixed14_v1.json").read_text())
    assert config["source_scope"] == "frozen_888_card_calibration_only"
    assert "618_card_internal_validation" in config["excluded_scopes"]
    assert "377_card_locked_audit" in config["excluded_scopes"]
    assert config["broad_band"]["runtime_truth_dependent"] is False
    assert config["fusion"]["truth_or_confidence_selected"] is False


def test_broad_band_contains_owner_with_context_on_every_side():
    config = json.loads((ROOT / "configs" / "phase2l_a_fixed14_v1.json").read_text())
    x0, y0, x1, y1 = config["broad_band"]["bbox_xyxy"]
    ox0, oy0, ox1, oy1 = config["broad_band"]["owner_bbox_xyxy"]
    assert x0 < ox0 < ox1 < x1 + 1
    assert y0 < oy0 < oy1 < y1
    assert x1 > ox1


def test_letterbox_preserves_aspect_ratio_and_shape():
    source = np.zeros((180, 650, 3), dtype=np.uint8)
    output = p.letterbox(source, (64, 448))
    assert output.shape == (64, 448, 3)
    # 650:180 fitted to height gives a 231-pixel-wide non-padding image.
    assert np.count_nonzero(np.any(output != 245, axis=2)) == 64 * 231


def test_edit_alignment_counts_insert_delete_substitute_without_values():
    aligned = p._edit_alignment("0123", "04235")
    counts = {name: sum(item[0] == name for item in aligned)
              for name in ("M", "S", "D", "I")}
    assert counts == {"M": 3, "S": 1, "D": 0, "I": 1}


def test_aggregate_writer_rejects_id_shaped_literals(tmp_path):
    path = tmp_path / "aggregate.json"
    try:
        p.write_json(path, {"unsafe": "1" * 14})
    except ValueError as error:
        assert "ID-shaped" in str(error)
    else:
        raise AssertionError("privacy guard did not reject an ID-shaped literal")
