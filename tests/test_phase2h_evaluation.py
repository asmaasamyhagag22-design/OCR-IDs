import inspect

from scripts import phase2h_evaluate as evaluation


def test_runtime_localization_happens_before_truth_lookup():
    source = inspect.getsource(evaluation.evaluate_card)
    assert source.index("localize(") < source.index('inputs["lookup"]')


def test_persisted_comparison_contains_no_raw_digits():
    result = evaluation.comparison(
        "20301011234567", "20301011234568", 0.91, b"synthetic-test-secret")
    serialized = evaluation.canonical_json(result)
    assert "20301011234567" not in serialized
    assert "20301011234568" not in serialized
    assert result["read_token"].startswith("h2h_")
    assert result["read_length"] == 14


def test_equality_token_is_stable_but_does_not_embed_input():
    first = evaluation.equality_token(b"synthetic-test-secret", "20301011234567")
    second = evaluation.equality_token(b"synthetic-test-secret", "20301011234567")
    assert first == second
    assert "20301011234567" not in first


def test_complete_wrong_synthetic_read_is_counted_and_never_repaired():
    result = evaluation.comparison(
        "30104151402347", "30104151402348", 0.99, b"synthetic-test-secret")
    assert result["read_length"] == 14
    assert result["exact"] is False
    assert result["wrong_digits"] == 1
    assert "repair" not in result
