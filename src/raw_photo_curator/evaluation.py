from itertools import combinations


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
