from pathlib import Path

from raw_photo_curator.active_selection import select_active_candidates
from raw_photo_curator.models import Metrics, Result


def item(name: str, score: float) -> Result:
    return Result(Path(name), score, score, Metrics(*(score for _ in range(9))), (), "x.jpg")


def test_active_selection_balances_quality_uncertainty_and_diversity():
    photos = [item("high.arw", 95), item("uncertain.arw", 78), item("diverse.arw", 76)]
    scores = {str(photo.path): photo.keep_score for photo in photos}
    learned = {"high.arw": 95, "uncertain.arw": 50, "diverse.arw": 55}
    contexts = {
        "high.arw": {"embedding": [0.0, 0.0]},
        "uncertain.arw": {"embedding": [0.0, 0.0]},
        "diverse.arw": {"embedding": [1.0, 1.0]},
    }
    selected = select_active_candidates(photos, scores, learned, contexts, 3)
    assert selected[0].path.name == "uncertain.arw"
    assert selected[1].path.name == "diverse.arw"
