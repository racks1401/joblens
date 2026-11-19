"""Evaluation metrics, written by hand so they are easy to check: P@k, nDCG@k, MRR, Recall@k, set F1,
Spearman, accuracy, confusion matrix, PR-AUC (average precision), recall at a precision floor."""

from __future__ import annotations

import math
from collections.abc import Hashable, Iterable, Sequence
from typing import Any


def precision_at_k(rels: Sequence[int], k: int, threshold: int = 1) -> float:
    """Fraction of the top k with relevance >= threshold (missing positions count as not relevant)."""
    return sum(1 for r in rels[:k] if r >= threshold) / k


def dcg(gains: Sequence[float], k: int) -> float:
    """Linear-gain DCG: sum g_i / log2(i + 1), i from 1."""
    return sum(g / math.log2(i + 1) for i, g in enumerate(gains[:k], start=1))


def ndcg_at_k(gains: Sequence[float], all_gains: Iterable[float], k: int = 10) -> float:
    """DCG of the ranking divided by DCG of the ideal ordering of every judged gain for this query."""
    ideal = dcg(sorted(all_gains, reverse=True), k)
    return dcg(gains, k) / ideal if ideal > 0 else 0.0


def mrr(rels: Sequence[int], target: int = 2) -> float:
    """Reciprocal rank of the first item with relevance >= target (0 if none)."""
    for i, r in enumerate(rels, start=1):
        if r >= target:
            return 1.0 / i
    return 0.0


def recall_at_k(retrieved: Sequence[Any], relevant: set[Any], k: int) -> float:
    """|top k ∩ relevant| / |relevant| (0 if nothing is relevant)."""
    if not relevant:
        return 0.0
    return len(set(retrieved[:k]) & relevant) / len(relevant)


def set_prf(pred: Iterable[Hashable] | None, gold: Iterable[Hashable] | None) -> tuple[float, float, float]:
    """Precision, recall, F1 of two sets. Both empty -> (1, 1, 1)."""
    p, g = set(pred or []), set(gold or [])
    if not p and not g:
        return 1.0, 1.0, 1.0
    tp = len(p & g)
    prec = tp / len(p) if p else 0.0
    rec = tp / len(g) if g else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1


def _ranks(xs: Sequence[float]) -> list[float]:
    """Average ranks (1-based) with ties sharing the mean rank."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for t in range(i, j + 1):
            ranks[order[t]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    """Spearman rank correlation = Pearson correlation of average ranks. None if undefined."""
    if len(x) != len(y) or len(x) < 2:
        return None
    rx, ry = _ranks(x), _ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return cov / (vx * vy) if vx and vy else None


def accuracy(pred: Sequence[Hashable], gold: Sequence[Hashable]) -> float:
    return sum(p == g for p, g in zip(pred, gold, strict=True)) / len(gold) if gold else 0.0


def confusion_matrix(pred: Sequence[str], gold: Sequence[str], labels: Sequence[str]) -> dict[str, dict[str, int]]:
    """{gold_label: {pred_label: count}}."""
    m = {g: {p: 0 for p in labels} for g in labels}
    for p, g in zip(pred, gold, strict=True):
        m.setdefault(g, {x: 0 for x in labels}).setdefault(p, 0)
        m[g][p] += 1
    return m


def average_precision(y_true: Sequence[int], scores: Sequence[float]) -> float:
    """PR-AUC as average precision: mean of precision at each positive, ranked by score desc."""
    pairs = sorted(zip(scores, y_true, strict=True), key=lambda x: -x[0])
    pos = sum(y_true)
    if pos == 0:
        return 0.0
    tp, ap = 0, 0.0
    for i, (_, y) in enumerate(pairs, start=1):
        if y:
            tp += 1
            ap += tp / i
    return ap / pos


def recall_at_precision(y_true: Sequence[int], scores: Sequence[float], min_precision: float = 0.8) -> float:
    """Highest recall over thresholds whose precision >= min_precision (0 if none)."""
    pairs = sorted(zip(scores, y_true, strict=True), key=lambda x: -x[0])
    pos = sum(y_true)
    if pos == 0:
        return 0.0
    best, tp = 0.0, 0
    for i, (_, y) in enumerate(pairs, start=1):
        tp += y
        if tp / i >= min_precision:
            best = max(best, tp / pos)
    return best


def binary_f1(y_true: Sequence[int], y_pred: Sequence[int]) -> float:
    tp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t and p)
    fp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if not t and p)
    fn = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t and not p)
    return 2 * tp / (2 * tp + fp + fn) if tp else 0.0


def percentile(xs: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile, q in [0, 100]."""
    if not xs:
        return 0.0
    s = sorted(xs)
    pos = (len(s) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)
