"""Metric functions against hand-computed values."""

import math

import pytest

from evals import metrics as m


def test_precision_at_k() -> None:
    assert m.precision_at_k([2, 0, 1, 0, 0], 5) == 0.4
    assert m.precision_at_k([2, 1], 5) == 0.4  # missing positions are non-relevant


def test_ndcg_hand_computed() -> None:
    # ranking gains [1, 2, 0]; ideal [2, 1, 0]
    dcg = 1 / math.log2(2) + 2 / math.log2(3)
    idcg = 2 / math.log2(2) + 1 / math.log2(3)
    assert m.ndcg_at_k([1, 2, 0], [2, 1, 0], 10) == pytest.approx(dcg / idcg)
    assert m.ndcg_at_k([2, 1, 0], [0, 1, 2], 10) == pytest.approx(1.0)
    assert m.ndcg_at_k([0, 0], [0, 0], 10) == 0.0


def test_mrr_and_recall() -> None:
    assert m.mrr([0, 1, 2, 2]) == pytest.approx(1 / 3)
    assert m.mrr([1, 1]) == 0.0
    assert m.recall_at_k([1, 2, 3, 4], {2, 4, 9}, 3) == pytest.approx(1 / 3)
    assert m.recall_at_k([1], set(), 3) == 0.0


def test_set_prf() -> None:
    assert m.set_prf(["a", "b"], ["b", "c"]) == (0.5, 0.5, 0.5)
    assert m.set_prf([], []) == (1.0, 1.0, 1.0)
    assert m.set_prf(None, ["x"]) == (0.0, 0.0, 0.0)


def test_spearman() -> None:
    assert m.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert m.spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    # with ties: ranks x=[1.5,1.5,3], y=[1,2,3] -> pearson = 0.8660
    assert m.spearman([1, 1, 2], [1, 2, 3]) == pytest.approx(math.sqrt(3) / 2)
    assert m.spearman([1, 1], [1, 2]) is None


def test_accuracy_confusion() -> None:
    cm = m.confusion_matrix(["A", "B", "A"], ["A", "A", "B"], ["A", "B"])
    assert cm == {"A": {"A": 1, "B": 1}, "B": {"A": 1, "B": 0}}
    assert m.accuracy(["A", "B", "A"], ["A", "A", "A"]) == pytest.approx(2 / 3)


def test_average_precision_and_recall_at_precision() -> None:
    y, s = [1, 0, 1, 0], [0.9, 0.8, 0.7, 0.1]
    assert m.average_precision(y, s) == pytest.approx((1 / 1 + 2 / 3) / 2)
    assert m.recall_at_precision(y, s, 0.8) == 0.5  # only the first threshold has precision >= 0.8
    assert m.recall_at_precision(y, s, 0.6) == 1.0  # top-3 precision 0.667
    assert m.binary_f1([1, 0, 1, 1], [1, 1, 0, 1]) == pytest.approx(2 * 2 / (2 * 2 + 1 + 1))


def test_percentile() -> None:
    assert m.percentile([1, 2, 3, 4], 50) == 2.5
    assert m.percentile([5], 95) == 5
