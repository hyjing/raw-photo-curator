from dataclasses import astuple
from math import sqrt

import numpy as np

from .models import Result


def _vector(result: Result) -> np.ndarray:
    return np.asarray(astuple(result.metrics), dtype=np.float64) / 100.0


def recommendation_scores(
    results: list[Result], feedback: dict[str, dict], priors: dict[str, float] | None = None
) -> dict[str, float]:
    positives: list[np.ndarray] = []
    negatives: list[np.ndarray] = []
    for result in results:
        choice = feedback.get(str(result.path), {}).get("choice")
        if choice in {"keep", "edit"}:
            positives.append(_vector(result))
        elif choice == "reject":
            negatives.append(_vector(result))

    reviewed = len(positives) + len(negatives)
    alpha = min(0.40, reviewed / 30 * 0.40)
    positive_center = np.mean(positives, axis=0) if positives else None
    negative_center = np.mean(negatives, axis=0) if negatives else None
    dimension_scale = sqrt(len(astuple(results[0].metrics))) if results else 1.0
    output: dict[str, float] = {}
    for result in results:
        vector = _vector(result)
        preference = 50.0
        if positive_center is not None and negative_center is not None:
            positive_distance = float(np.linalg.norm(vector - positive_center))
            negative_distance = float(np.linalg.norm(vector - negative_center))
            preference = 100 * negative_distance / max(0.001, positive_distance + negative_distance)
        elif positive_center is not None:
            preference = 100 * (1 - min(1.0, float(np.linalg.norm(vector - positive_center)) / dimension_scale))
        elif negative_center is not None:
            preference = 100 * min(1.0, float(np.linalg.norm(vector - negative_center)) / dimension_scale)
        prior = priors.get(str(result.path), result.keep_score) if priors else result.keep_score
        score = prior * (1 - alpha) + preference * alpha
        output[str(result.path)] = round(float(score), 1)
    return output
