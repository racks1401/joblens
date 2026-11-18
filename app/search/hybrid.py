"""Reciprocal Rank Fusion."""

from __future__ import annotations

from collections.abc import Sequence


def rrf(lists: Sequence[Sequence[int]], k: int = 60, top: int = 50) -> list[tuple[int, float]]:
    """rrf(job) = sum over lists of 1 / (k + rank), rank from 1. Keep top N, normalize by the max to 0..1.

    Ties are broken by first appearance (earlier list, better rank).
    """
    scores: dict[int, float] = {}
    order: dict[int, int] = {}
    for li, ids in enumerate(lists):
        for rank, jid in enumerate(ids, start=1):
            scores[jid] = scores.get(jid, 0.0) + 1.0 / (k + rank)
            order.setdefault(jid, li * 10_000 + rank)
    ranked = sorted(scores.items(), key=lambda x: (-x[1], order[x[0]]))[:top]
    if not ranked:
        return []
    mx = ranked[0][1]
    return [(jid, sc / mx) for jid, sc in ranked]
