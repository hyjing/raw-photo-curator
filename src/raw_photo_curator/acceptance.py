from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .catalog import Catalog
from .evaluation import grouping_metrics


def acceptance_report(
    report_directory: Path,
    *,
    expected_photos: int | None = None,
    group_labels: Path | None = None,
) -> dict[str, object]:
    catalog_path = report_directory / "catalog.sqlite3"
    feedback_path = report_directory / "feedback.sqlite3"
    if not catalog_path.is_file():
        raise FileNotFoundError(f"catalog not found: {catalog_path}")

    with sqlite3.connect(catalog_path) as connection:
        photos = _count(connection, "photos")
        cached = connection.execute(
            "SELECT count(DISTINCT photo_id) FROM analysis_cache"
        ).fetchone()[0]
        criteria = connection.execute(
            "SELECT count(DISTINCT criterion_id) FROM criterion_results"
        ).fetchone()[0]
        failed_jobs = connection.execute(
            "SELECT count(*) FROM analysis_jobs WHERE status = 'failed'"
        ).fetchone()[0]
    coverage = cached / photos if photos else 0.0

    feedback = group_feedback = models = 0
    if feedback_path.is_file():
        with sqlite3.connect(feedback_path) as connection:
            feedback = _count(connection, "profile_feedback")
            group_feedback = _count(connection, "group_feedback")
            models = _count(connection, "preference_models")

    checks: dict[str, dict[str, object]] = {
        "photo_inventory": _check(
            expected_photos is None or photos == expected_photos,
            photos,
            f"expected {expected_photos}" if expected_photos is not None else "inventory present",
        ),
        "warm_cache_coverage": _check(coverage >= 0.95, round(coverage, 4), "at least 0.95"),
        "analysis_jobs": _check(failed_jobs == 0, failed_jobs, "zero failed jobs"),
        "criterion_coverage": _check(criteria >= 17, criteria, "at least 17 criteria"),
        "real_profile_feedback": _check(
            feedback >= 5,
            feedback,
            "at least 5 real decisions; 20+ recommended for a learning curve",
        ),
        "group_corrections": _check(
            group_feedback > 0, group_feedback, "at least one persisted human correction"
        ),
        "preference_model": _check(models > 0, models, "at least one trained profile model"),
    }
    evaluation_paths = sorted(report_directory.glob("personal-evaluation-*.json"))
    evaluation = None
    if evaluation_paths:
        evaluation = json.loads(evaluation_paths[-1].read_text(encoding="utf-8"))
    checks["personal_evaluation"] = _check(
        bool(
            evaluation
            and evaluation.get("test_count", 0) > 0
            and isinstance(evaluation.get("personalization_improved"), bool)
        ),
        evaluation,
        "reproducible holdout report that truthfully states whether personalization improved",
    )

    grouping: dict[str, object] | None = None
    if group_labels is not None:
        labels = json.loads(group_labels.read_text(encoding="utf-8"))
        with Catalog(catalog_path) as catalog:
            predicted = [
                [str(member["path"]) for member in group["members"]]
                for group in catalog.groups()
            ]
        grouping = grouping_metrics(predicted, labels["groups"])
        checks["labeled_group_evaluation"] = _check(
            grouping["labeled_pairs"] > 0,
            grouping,
            "non-empty manually labeled pair set",
        )
    else:
        checks["labeled_group_evaluation"] = {
            "status": "missing",
            "value": None,
            "requirement": "pass --group-labels with a manually labeled JSON set",
        }

    blocking = [name for name, check in checks.items() if check["status"] != "pass"]
    return {
        "schema": "raw-curator-acceptance-v1",
        "report_directory": str(report_directory.resolve()),
        "ready": not blocking,
        "blocking_checks": blocking,
        "checks": checks,
        "grouping_metrics": grouping,
        "personal_evaluation": evaluation,
    }


def _count(connection: sqlite3.Connection, table: str) -> int:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if not exists:
        return 0
    return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def _check(passed: bool, value: object, requirement: str) -> dict[str, object]:
    return {
        "status": "pass" if passed else "missing",
        "value": value,
        "requirement": requirement,
    }
