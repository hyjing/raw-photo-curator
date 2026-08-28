from raw_photo_curator.evaluation import grouping_metrics


def test_grouping_metrics_use_pairwise_precision_and_recall():
    predicted = [["a", "b", "c"], ["d", "e"]]
    labeled = [["a", "b"], ["b", "c"], ["d", "x"]]
    metrics = grouping_metrics(predicted, labeled)
    assert metrics["predicted_pairs"] == 4
    assert metrics["labeled_pairs"] == 3
    assert metrics["true_positive_pairs"] == 2
    assert metrics["precision"] == 0.5
    assert metrics["recall"] == 0.6667
