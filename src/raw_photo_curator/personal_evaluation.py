from __future__ import annotations

import json
import sqlite3
from hashlib import sha256
from math import log2, sqrt
from pathlib import Path

import numpy as np

from .catalog import Catalog
from .models import Metrics, Result
from .profiles import BUILTIN_PROFILES, weighted_score
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
    labeled = [
        item
        for item in results
        if feedback.get(str(item.path), {}).get("choice") in {"keep", "edit", "reject"}
    ]
    train, test = _stratified_holdout(labeled, feedback)
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


def _stratified_holdout(
    labeled: list[Result], feedback: dict[str, dict]
) -> tuple[list[Result], list[Result]]:
    """Make a deterministic 25% holdout while preserving each available class."""
    train: list[Result] = []
    test: list[Result] = []
    for positive in (True, False):
        group = [
            item
            for item in labeled
            if _positive(feedback[str(item.path)].get("choice")) is positive
        ]
        group.sort(
            key=lambda item: sha256(str(item.path).encode("utf-8")).digest()
        )
        # Pairwise training needs two examples from each class. With fewer than
        # three examples, keep the whole class in training and report no test pair.
        holdout = max(1, round(len(group) * 0.25)) if len(group) >= 3 else 0
        holdout = min(holdout, max(0, len(group) - 2))
        test.extend(group[:holdout])
        train.extend(group[holdout:])
    train.sort(key=lambda item: str(item.path))
    test.sort(key=lambda item: str(item.path))
    return train, test


def evaluate_report_directory(report_directory: Path, profile_id: str = "travel") -> dict:
    """Reproduce the local holdout evaluation without requiring the web server."""
    results_data = json.loads((report_directory / "results.json").read_text(encoding="utf-8"))
    results = [
        Result(
            Path(item["path"]),
            float(item["keep_score"]),
            float(item["edit_score"]),
            Metrics(**item["metrics"]),
            tuple(item["notes"]),
            str(item["thumbnail"]),
        )
        for item in results_data
    ]
    feedback_database = report_directory / "feedback.sqlite3"
    with sqlite3.connect(feedback_database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT path, choice, rating, tags, note, updated_at FROM profile_feedback "
            "WHERE profile_id = ?",
            (profile_id,),
        ).fetchall()
    feedback = {str(row["path"]): dict(row) for row in rows}
    profile = next((item for item in BUILTIN_PROFILES if item.id == profile_id), None)
    if profile is None:
        raise ValueError(f"offline evaluation currently requires a built-in profile: {profile_id}")
    with Catalog(report_directory / "catalog.sqlite3") as catalog:
        contexts = catalog.preference_contexts()
        priors = {
            str(result.path): weighted_score(
                result, profile, catalog.criteria_for_path(str(result.path))
            )
            for result in results
        }
    evaluation = evaluate_personalization(results, feedback, contexts, priors, profile_id)
    evaluation.update(
        {
            "schema": "raw-curator-personal-evaluation-v1",
            "profile_id": profile_id,
            "labeled_count": sum(
                item.get("choice") in {"keep", "edit", "reject"}
                for item in feedback.values()
            ),
        }
    )
    return evaluation
