from pathlib import Path

from raw_photo_curator.models import Metrics, Result
from raw_photo_curator.recommendation import recommendation_scores


def result(name: str, value: float) -> Result:
    metrics = Metrics(*(value for _ in range(9)))
    return Result(Path(name), value, value, metrics, (), f"{name}.jpg")


def test_feedback_moves_similar_photo_toward_preference():
    liked = result("liked.arw", 80)
    similar = result("similar.arw", 78)
    different = result("different.arw", 20)
    feedback = {
        "liked.arw": {"choice": "keep"},
        "different.arw": {"choice": "reject"},
    }
    scores = recommendation_scores([liked, similar, different], feedback)
    assert scores["similar.arw"] > scores["different.arw"]


def test_no_feedback_preserves_objective_order():
    high = result("high.arw", 80)
    low = result("low.arw", 40)
    scores = recommendation_scores([high, low], {})
    assert scores["high.arw"] > scores["low.arw"]
