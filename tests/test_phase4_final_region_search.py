"""F9 (configs/phase4_final_rule_v1.json amendment 2): the trigger, the choice, the acceptance, the regions."""
from __future__ import annotations

import numpy as np
import pytest

from src.service import region_search as rs

pytestmark = pytest.mark.service  # the fast shipped-code target (scripts/test_service.sh)

T = 0.966875
#: century digit 9: structurally impossible, so no fixture can be a real ID
INVALID = "91234567890123"


@pytest.fixture
def valid_pair(monkeypatch):
    """Two strings the structural check treats as valid, without writing a valid ID in the source."""
    good = {"VALID_A_______", "VALID_B_______"}
    monkeypatch.setattr(rs.sp, "structurally_valid", lambda digits: digits in good)
    return sorted(good)


def test_the_trigger_never_fires_on_a_valid_reading_at_or_above_the_threshold(valid_pair):
    a, _ = valid_pair
    assert not rs.triggered(True, a, T, T)
    assert not rs.triggered(True, a, 0.999999, T)
    assert rs.triggered(True, a, T - 1e-9, T)


def test_the_trigger_fires_on_a_detector_failure_and_an_invalid_reading(valid_pair):
    assert rs.triggered(False, None, None, T)
    assert rs.triggered(True, "", None, T)
    assert rs.triggered(True, INVALID, 0.99, T)


def test_the_choice_is_validity_first_then_margin(valid_pair):
    a, b = valid_pair
    cands = [rs.Candidate("x", INVALID, 0.999), rs.Candidate("y", a, 0.40), rs.Candidate("z", b, 0.70)]
    assert rs.best_of(cands).source == "z"


def test_a_valid_low_margin_reading_is_replaced_only_by_a_different_confident_candidate(valid_pair):
    a, b = valid_pair
    assert not rs.accept(rs.Candidate("c", b, T - 0.001), True, a, T, 0.3)
    assert not rs.accept(rs.Candidate("c", a, 0.999), True, a, T, 0.3)
    assert rs.accept(rs.Candidate("c", b, T), True, a, T, 0.3)


def test_an_unread_or_invalid_card_takes_the_best_valid_candidate_above_accept_margin(valid_pair):
    a, _ = valid_pair
    assert rs.accept(rs.Candidate("c", a, 0.5), False, None, T, 0.5)
    assert not rs.accept(rs.Candidate("c", a, 0.49), False, None, T, 0.5)
    assert rs.accept(rs.Candidate("c", a, 0.8), True, INVALID, T, 0.8)
    assert not rs.accept(rs.Candidate("c", INVALID, 0.99), False, None, T, 0.3)
    assert not rs.accept(None, False, None, T, 0.3)


def test_search_stops_at_the_first_confident_valid_candidate_and_honours_acceptance(monkeypatch, valid_pair):
    a, b = valid_pair
    seen = []

    def fake(rgb, runtime, first, admitted):
        for c in (rs.Candidate("one", INVALID, 0.9), rs.Candidate("two", a, 0.99), rs.Candidate("three", b, 1.0)):
            seen.append(c.source)
            yield c

    monkeypatch.setattr(rs, "all_candidates", fake)
    chosen, info = rs.search(np.zeros((4, 4, 3), np.uint8), None, None, False, None, T, 0.5)
    assert chosen.source == "two" and seen == ["one", "two"] and info["accepted"]


def test_regions_are_the_whole_image_the_trim_and_four_overlapping_halves():
    rgb = np.full((200, 400, 3), 255, np.uint8)
    rgb[40:160, 60:340] = 30                           # a dark card on a white page
    names = [name for name, _ in rs.regions(rgb)]
    assert names == ["full", "trimmed", "top", "bottom", "left", "right"]
    trimmed = dict(rs.regions(rgb))["trimmed"]
    assert trimmed.shape[:2] == (120, 280)
    top = dict(rs.regions(rgb))["top"]
    assert top.shape[0] == 110                         # 0.55 of 200


def test_no_trim_when_there_is_no_margin():
    rgb = np.random.default_rng(0).integers(0, 255, (100, 160, 3), dtype=np.uint8)
    assert rs.trim_uniform_margin(rgb) is None
