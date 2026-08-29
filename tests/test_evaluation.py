from pathlib import Path

from PIL import Image

from raw_photo_curator.catalog import Catalog
from raw_photo_curator.cli import analyze
from raw_photo_curator.evaluation import evaluate_manual_corrections, grouping_metrics


def test_grouping_metrics_use_pairwise_precision_and_recall():
    predicted = [["a", "b", "c"], ["d", "e"]]
    labeled = [["a", "b"], ["b", "c"], ["d", "x"]]
    metrics = grouping_metrics(predicted, labeled)
    assert metrics["predicted_pairs"] == 4
    assert metrics["labeled_pairs"] == 3
    assert metrics["true_positive_pairs"] == 2
    assert metrics["precision"] == 0.5
    assert metrics["recall"] == 0.6667


def test_manual_correction_evaluation_is_scoped_to_labeled_photos(tmp_path: Path):
    photos = tmp_path / "photos"
    report = tmp_path / "report"
    photos.mkdir()
    for index in range(3):
        Image.new("RGB", (40, 30), (index * 40, 80, 120)).save(photos / f"{index}.jpg")
    analyze(photos, report)
    with Catalog(report / "catalog.sqlite3") as catalog:
        records = catalog.photo_records()
        catalog.save_manual_partitions(
            [],
            [tuple(str(item["id"]) for item in records[:2])],
            "related",
        )
    result = evaluate_manual_corrections(report / "catalog.sqlite3")
    assert result["labeled_photos"] == 2
    assert result["labeled_groups"] == 1
    assert result["metrics"]["labeled_pairs"] == 1
