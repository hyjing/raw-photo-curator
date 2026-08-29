import json
from pathlib import Path

from PIL import Image

from raw_photo_curator.acceptance import acceptance_report
from raw_photo_curator.catalog import Catalog
from raw_photo_curator.cli import analyze
from raw_photo_curator.grouping import SimilarityGroup


def test_acceptance_separates_automated_evidence_from_missing_human_labels(tmp_path: Path):
    photos = tmp_path / "photos"
    report = tmp_path / "report"
    photos.mkdir()
    Image.new("RGB", (48, 32), "navy").save(photos / "one.jpg")
    analyze(photos, report)

    result = acceptance_report(report, expected_photos=1)
    assert result["checks"]["photo_inventory"]["status"] == "pass"
    assert result["checks"]["warm_cache_coverage"]["status"] == "pass"
    assert result["checks"]["real_profile_feedback"]["status"] == "missing"
    assert result["checks"]["labeled_group_evaluation"]["status"] == "missing"
    assert not result["ready"]


def test_acceptance_reports_metrics_for_manual_group_labels(tmp_path: Path):
    report = tmp_path / "report"
    photos = tmp_path / "photos"
    photos.mkdir()
    Image.new("RGB", (48, 32), "navy").save(photos / "a.jpg")
    Image.new("RGB", (48, 32), "orange").save(photos / "b.jpg")
    analyze(photos, report)
    with Catalog(report / "catalog.sqlite3") as catalog:
        records = catalog.photo_records()
        catalog.replace_automatic_groups(
            [SimilarityGroup("g1", "burst", 1.0, tuple(str(item["id"]) for item in records))]
        )
    labels = tmp_path / "labels.json"
    labels.write_text(
        json.dumps({"groups": [[str(photos / "a.jpg"), str(photos / "b.jpg")]]})
    )

    result = acceptance_report(report, group_labels=labels)
    assert result["grouping_metrics"]["precision"] == 1.0
    assert result["grouping_metrics"]["recall"] == 1.0
    assert result["checks"]["labeled_group_evaluation"]["status"] == "pass"
