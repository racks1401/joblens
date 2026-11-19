"""E1 evaluation core: unlabeled retrieved jobs count as 0; metrics match hand-computed values."""

import math

import pytest

from evals.e1 import evaluate


def test_evaluate_hand_computed() -> None:
    labels = {"p": {"a": 2, "b": 0, "c": 1}}
    ranked = {"good": ["a", "c", "x", "b"], "bad": ["x", "b", "y", "a"]}
    res = evaluate(labels, lambda pid, m: ranked[m], ["good", "bad"])
    g, b = res["good"]["per_profile"]["p"], res["bad"]["per_profile"]["p"]
    assert g["p@5"] == pytest.approx(2 / 5) and g["mrr"] == 1.0 and g["ndcg@10"] == pytest.approx(1.0)
    assert g["unlabeled_in_top10"] == 1  # "x" is unlabeled -> counted as not relevant
    ideal = 2 / math.log2(2) + 1 / math.log2(3)
    assert b["ndcg@10"] == pytest.approx((2 / math.log2(5)) / ideal) and b["mrr"] == pytest.approx(1 / 4)
    assert res["bad"]["unlabeled_in_top10"] == 2
