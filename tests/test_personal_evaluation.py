from pathlib import Path

from raw_photo_curator.models import Metrics, Result
from raw_photo_curator.personal_evaluation import evaluate_personalization


def photo(index: int) -> Result:
    value = float(index * 5)
    return Result(Path(f"{index:02}.arw"), value, value, Metrics(*(value for _ in range(9))), (), "x.jpg")


def test_local_evaluation_is_reproducible_and_reports_baselines():
    results = [photo(index) for index in range(20)]
    feedback = {
        str(item.path): {"choice": "keep" if item.keep_score >= 50 else "reject"}
        for item in results
    }
    priors = {str(item.path): item.keep_score for item in results}
    first = evaluate_personalization(results, feedback, {}, priors, "travel")
    second = evaluate_personalization(results, feedback, {}, priors, "travel")
    assert first == second
    assert first["model_ready"]
    assert first["generic_prior"]["pairwise_accuracy"] == 1.0
    assert first["personal_ranker"]["ndcg"] == 1.0
    assert [point["feedback_count"] for point in first["learning_curve"]] == [5, 10, 15, 15]
