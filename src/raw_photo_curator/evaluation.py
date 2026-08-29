from itertools import combinations
from pathlib import Path

from .catalog import Catalog
from .grouping import build_groups


def _pairs(groups: list[list[str]]) -> set[tuple[str, str]]:
    return {
        tuple(sorted(pair))
        for group in groups
        for pair in combinations(set(group), 2)
    }


def grouping_metrics(
    predicted_groups: list[list[str]], labeled_groups: list[list[str]]
) -> dict[str, float | int]:
    predicted = _pairs(predicted_groups)
    labeled = _pairs(labeled_groups)
    true_positive = len(predicted & labeled)
    precision = true_positive / len(predicted) if predicted else (1.0 if not labeled else 0.0)
    recall = true_positive / len(labeled) if labeled else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "predicted_pairs": len(predicted),
        "labeled_pairs": len(labeled),
        "true_positive_pairs": true_positive,
    }


def evaluate_manual_corrections(catalog_path: Path) -> dict[str, object]:
    """Compare fresh automatic grouping with the user-corrected label universe."""
    with Catalog(catalog_path) as catalog:
        records = catalog.photo_records()
        corrected = [group for group in catalog.groups() if group["manually_corrected"]]
    labels = [
        [str(member["path"]) for member in group["members"]]
        for group in corrected
    ]
    universe = {path for group in labels for path in group}
    by_id = {str(record["id"]): str(record["path"]) for record in records}
    predicted = []
    for group in build_groups(records):
        paths = [
            by_id[photo_id]
            for photo_id in group.photo_ids
            if by_id[photo_id] in universe
        ]
        if len(paths) >= 2:
            predicted.append(paths)
    return {
        "schema": "raw-curator-group-correction-evaluation-v1",
        "labeled_photos": len(universe),
        "labeled_groups": len(labels),
        "predicted_groups_in_label_universe": len(predicted),
        "labels": {"groups": labels},
        "metrics": grouping_metrics(predicted, labels),
    }
