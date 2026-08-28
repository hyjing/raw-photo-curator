from __future__ import annotations

from math import log2, sqrt

import numpy as np

from .models import Result
from .ranker import predict, train_pairwise


def _positive(choice: str | None) -> bool:
    return choice in {"keep", "edit"}


def _ranking_metrics(items: list[Result], labels: dict[str, dict], scores: dict[str, float]) -> dict:
    positives = [item for item in items if _positive(labels[str(item.path)].get("choice"))]
    negatives = [item for item in items if labels[str(item.path)].get("choice") == "reject"]
    pairs = [(left, right) for left in positives for right in negatives]
    outcomes = [scores[str(left.path)] > scores[str(right.path)] for left, right in pairs]
    pairwise = float(np.mean(outcomes)) if outcomes else 0.0
    ranked = sorted(items, key=lambda item: scores[str(item.path)], reverse=True)
    k = min(5, len(ranked))
    top_k = sum(_positive(labels[str(item.path)].get("choice")) for item in ranked[:k]) / max(1, min(k, len(positives)))
    dcg = sum(
        int(_positive(labels[str(item.path)].get("choice"))) / log2(index + 2)
        for index, item in enumerate(ranked)
    )
    ideal = sum(1 / log2(index + 2) for index in range(len(positives)))
    rng = np.random.default_rng(42)
    samples = []
    if outcomes:
        values = np.asarray(outcomes, dtype=np.float64)
        samples = [float(rng.choice(values, len(values), replace=True).mean()) for _ in range(500)]
    margin = 1.96 * sqrt(pairwise * (1 - pairwise) / max(1, len(outcomes)))
    return {
        "pairwise_accuracy": round(pairwise, 4),
        "pairwise_pairs": len(pairs),
        "pairwise_ci95": [
            round(float(np.percentile(samples, 2.5)), 4),
            round(float(np.percentile(samples, 97.5)), 4),
        ] if samples else [round(max(0.0, pairwise - margin), 4), round(min(1.0, pairwise + margin), 4)],
        "top_5_hit_rate": round(top_k, 4),
        "ndcg": round(dcg / ideal, 4) if ideal else 0.0,
    }


def evaluate_personalization(
    results: list[Result], feedback: dict[str, dict], contexts: dict[str, dict],
    priors: dict[str, float], profile_id: str,
) -> dict:
    labeled = [item for item in results if feedback.get(str(item.path), {}).get("choice") in {"keep", "edit", "reject"}]
    # Stable ordering makes the same local database produce the same report.
    labeled.sort(key=lambda item: str(item.path))
    train = [item for index, item in enumerate(labeled) if index % 4 != 0]
    test = [item for index, item in enumerate(labeled) if index % 4 == 0]
    train_feedback = {str(item.path): feedback[str(item.path)] for item in train}
    model = train_pairwise(train, train_feedback, contexts, profile_id)
    baseline_scores = {str(item.path): item.keep_score for item in test}
    explicit_scores = {str(item.path): priors[str(item.path)] for item in test}
    personal_scores = {
        str(item.path): predict(model, item, contexts.get(str(item.path))) for item in test
    } if model else explicit_scores
    report = {
        "training_count": len(train),
        "test_count": len(test),
        "model_ready": model is not None,
        "generic_prior": _ranking_metrics(test, feedback, baseline_scores) if test else {},
        "explicit_profile": _ranking_metrics(test, feedback, explicit_scores) if test else {},
        "personal_ranker": _ranking_metrics(test, feedback, personal_scores) if test else {},
    }
    report["personalization_improved"] = bool(
        model and report["personal_ranker"]["pairwise_accuracy"]
        > report["explicit_profile"]["pairwise_accuracy"]
    )
    curves = []
    for count in (5, 10, 20, 50):
        prefix = train[:count]
        prefix_feedback = {str(item.path): feedback[str(item.path)] for item in prefix}
        prefix_model = train_pairwise(prefix, prefix_feedback, contexts, profile_id)
        metrics = None
        if prefix_model and test:
            scores = {str(item.path): predict(prefix_model, item, contexts.get(str(item.path))) for item in test}
            metrics = _ranking_metrics(test, feedback, scores)
        curves.append({"feedback_count": min(count, len(prefix)), "metrics": metrics})
    report["learning_curve"] = curves
    return report
