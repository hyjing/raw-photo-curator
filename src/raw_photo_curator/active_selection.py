from __future__ import annotations

import numpy as np

from .models import Result


def select_active_candidates(
    items: list[Result], recommendation_scores: dict[str, float],
    learned_scores: dict[str, float] | None, contexts: dict[str, dict], count: int,
) -> list[Result]:
    """Greedy mix of ranking quality, uncertainty, and scene diversity."""
    remaining = list(items)
    selected: list[Result] = []
    while remaining and len(selected) < count:
        best = max(
            remaining,
            key=lambda item: _utility(
                item, selected, recommendation_scores, learned_scores, contexts
            ),
        )
        selected.append(best)
        remaining.remove(best)
    return selected


def _utility(
    item: Result, selected: list[Result], scores: dict[str, float],
    learned: dict[str, float] | None, contexts: dict[str, dict],
) -> float:
    path = str(item.path)
    quality = scores[path] / 100.0
    uncertainty = 0.0 if learned is None else 1.0 - abs(learned[path] - 50.0) / 50.0
    embedding = contexts.get(path, {}).get("embedding")
    diversity = 0.5
    if embedding and selected:
        vector = np.asarray(embedding, dtype=np.float64)
        distances = []
        for peer in selected:
            other = contexts.get(str(peer.path), {}).get("embedding")
            if other:
                distances.append(float(np.linalg.norm(vector - np.asarray(other))))
        if distances:
            diversity = min(1.0, min(distances) / 2.0)
    return 0.60 * quality + 0.25 * uncertainty + 0.15 * diversity
