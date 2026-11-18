"""Reciprocal Rank Fusion (Cormack, Clarke and Buettcher, SIGIR 2009).

score(d) = sum over rankings r containing d of 1 / (k + rank_r(d)), with rank starting at 1.
RRF only needs ranks, so vector distances and full-text scores, which are on different
scales, can be combined without normalisation.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence


def reciprocal_rank_fusion[T: Hashable](
    rankings: Sequence[Sequence[T]], k: int = 60
) -> list[tuple[T, float]]:
    if k <= 0:
        raise ValueError("k must be positive")
    scores: dict[T, float] = {}
    first_seen: dict[T, int] = {}
    order = 0
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            if item not in first_seen:
                first_seen[item] = order
                order += 1
    # Ties keep the order in which items were first seen, so results are deterministic.
    return sorted(scores.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))
