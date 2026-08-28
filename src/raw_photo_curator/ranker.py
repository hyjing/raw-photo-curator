from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from math import exp, log10

import numpy as np

from .models import Result

SCHEMA_VERSION = "preference-v1"
METRIC_NAMES = (
    "sharpness", "exposure", "highlights", "shadows", "contrast", "noise",
    "color", "white_balance", "composition", "horizon", "edge_integrity",
)
CRITERION_IDS = (
    "subject.saliency_concentration", "subject.background_separation",
    "depth.separation", "timing.motion_clarity",
)
CRITERION_NAMES = ("subject_saliency", "background_separation", "depth_separation", "motion_clarity")
EXIF_NAMES = ("focal_length", "aperture", "shutter_speed", "iso")
EMBEDDING_SIZE = 48
FEATURE_NAMES = (
    *METRIC_NAMES,
    *CRITERION_NAMES,
    *(f"group_{name}" for name in CRITERION_NAMES),
    *EXIF_NAMES,
    *(f"embedding_{index}" for index in range(EMBEDDING_SIZE)),
)


@dataclass(frozen=True)
class PreferenceModel:
    profile_id: str
    weights: tuple[float, ...]
    bias: float
    training_count: int
    positive_count: int
    negative_count: int
    validation_accuracy: float
    trained_at: str
    algorithm: str = "pairwise-logistic-l2"
    schema_version: str = SCHEMA_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, value: str) -> PreferenceModel:
        data = json.loads(value)
        data["weights"] = tuple(data["weights"])
        model = cls(**data)
        if model.schema_version != SCHEMA_VERSION or len(model.weights) != len(FEATURE_NAMES):
            raise ValueError("incompatible preference model")
        return model


def _bounded_log(value: object, scale: float) -> float:
    try:
        number = max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, log10(1 + number) / scale)


def feature_vector(result: Result, context: dict | None = None) -> np.ndarray:
    context = context or {}
    criteria = context.get("criteria", {})
    metadata = context.get("metadata", {})
    embedding = list(context.get("embedding") or ())[:EMBEDDING_SIZE]
    embedding.extend([0.0] * (EMBEDDING_SIZE - len(embedding)))
    values = [float(getattr(result.metrics, name)) / 100.0 for name in METRIC_NAMES]
    values.extend(float(criteria.get(name, {}).get("score") or 0.0) / 100.0 for name in CRITERION_IDS)
    values.extend(float(criteria.get(name, {}).get("group_percentile") or 0.0) / 100.0 for name in CRITERION_IDS)
    values.extend(
        (
            min(1.0, float(metadata.get("focal_length") or 0.0) / 600.0),
            min(1.0, float(metadata.get("aperture") or 0.0) / 22.0),
            _bounded_log(metadata.get("shutter_speed"), 1.0),
            _bounded_log(metadata.get("iso"), 5.0),
        )
    )
    values.extend(max(-1.0, min(1.0, float(value))) for value in embedding)
    return np.asarray(values, dtype=np.float64)


def train_pairwise(
    results: list[Result], feedback: dict[str, dict], contexts: dict[str, dict], profile_id: str
) -> PreferenceModel | None:
    positives = [item for item in results if feedback.get(str(item.path), {}).get("choice") in {"keep", "edit"}]
    negatives = [item for item in results if feedback.get(str(item.path), {}).get("choice") == "reject"]
    if len(positives) < 2 or len(negatives) < 2:
        return None
    pairs = [(positive, negative) for positive in positives for negative in negatives][:512]
    x = np.vstack([
        feature_vector(positive, contexts.get(str(positive.path)))
        - feature_vector(negative, contexts.get(str(negative.path)))
        for positive, negative in pairs
    ])
    # Both orientations avoid learning an arbitrary intercept from one-sided labels.
    x = np.vstack((x, -x))
    y = np.concatenate((np.ones(len(pairs)), np.zeros(len(pairs))))
    weights = np.zeros(x.shape[1], dtype=np.float64)
    bias = 0.0
    regularization = 0.35
    for step in range(350):
        logits = np.clip(x @ weights + bias, -20, 20)
        probabilities = 1.0 / (1.0 + np.exp(-logits))
        error = probabilities - y
        rate = 0.18 / (1 + step / 100)
        weights -= rate * ((x.T @ error) / len(y) + regularization * weights)
        bias -= rate * float(error.mean())
    accuracy = float(np.mean(((x @ weights + bias) >= 0) == y))
    return PreferenceModel(
        profile_id=profile_id,
        weights=tuple(round(float(value), 8) for value in weights),
        bias=round(bias, 8),
        training_count=len(positives) + len(negatives),
        positive_count=len(positives),
        negative_count=len(negatives),
        validation_accuracy=round(accuracy, 4),
        trained_at=datetime.now(UTC).isoformat(),
    )


def predict(model: PreferenceModel, result: Result, context: dict | None = None) -> float:
    logit = float(feature_vector(result, context) @ np.asarray(model.weights) + model.bias)
    return 100.0 / (1.0 + exp(-max(-20.0, min(20.0, logit))))


def learned_weight(model: PreferenceModel | None) -> float:
    if not model:
        return 0.0
    sample_factor = min(1.0, max(0.0, (model.training_count - 3) / 26))
    quality_factor = min(1.0, max(0.0, (model.validation_accuracy - 0.5) / 0.25))
    return round(0.4 * sample_factor * quality_factor, 3)


def contributions(
    model: PreferenceModel, result: Result, context: dict | None = None, limit: int = 3
) -> list[dict[str, float | str]]:
    effects = feature_vector(result, context) * np.asarray(model.weights)
    selected = sorted(range(len(effects)), key=lambda index: abs(effects[index]), reverse=True)[:limit]
    return [
        {"feature": FEATURE_NAMES[index], "effect": round(float(effects[index]), 3)}
        for index in selected if abs(effects[index]) >= 0.001
    ]
