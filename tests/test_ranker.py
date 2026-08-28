from pathlib import Path

from raw_photo_curator.models import Metrics, Result
from raw_photo_curator.ranker import (
    FEATURE_NAMES,
    PreferenceModel,
    contributions,
    learned_weight,
    predict,
    train_pairwise,
)


def result(name: str, sharpness: float, color: float = 50) -> Result:
    metrics = Metrics(
        sharpness, 50, 50, 50, 50, 50, color, 50, 50, 50, 50
    )
    return Result(Path(name), 50, 50, metrics, (), f"{name}.jpg")


def test_pairwise_ranker_learns_preference_direction():
    photos = [
        result("keep-1.arw", 95), result("keep-2.arw", 85),
        result("reject-1.arw", 15), result("reject-2.arw", 25),
        result("candidate.arw", 90),
    ]
    feedback = {
        "keep-1.arw": {"choice": "keep"}, "keep-2.arw": {"choice": "keep"},
        "reject-1.arw": {"choice": "reject"}, "reject-2.arw": {"choice": "reject"},
    }
    model = train_pairwise(photos, feedback, {}, "travel")
    assert model is not None
    assert predict(model, photos[-1]) > predict(model, photos[2])
    assert learned_weight(model) > 0
    assert contributions(model, photos[-1])[0]["feature"] == "sharpness"


def test_few_samples_fall_back_without_model():
    photos = [result("keep.arw", 90), result("reject.arw", 10)]
    feedback = {"keep.arw": {"choice": "keep"}, "reject.arw": {"choice": "reject"}}
    assert train_pairwise(photos, feedback, {}, "travel") is None
    assert learned_weight(None) == 0


def test_model_round_trip_is_small_and_profile_specific():
    model = PreferenceModel(
        profile_id="portrait", weights=tuple(0.01 for _ in FEATURE_NAMES), bias=0,
        training_count=20, positive_count=10, negative_count=10,
        validation_accuracy=0.8, trained_at="2026-01-01T00:00:00+00:00",
    )
    serialized = model.to_json()
    restored = PreferenceModel.from_json(serialized)
    assert restored.profile_id == "portrait"
    assert restored == model
    assert len(serialized.encode()) < 10_000_000
