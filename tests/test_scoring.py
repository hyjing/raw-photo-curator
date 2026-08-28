import numpy as np

from raw_photo_curator.scoring import measure, scores


def test_balanced_image_scores_are_bounded():
    gradient = np.linspace(0.15, 0.85, 96, dtype=np.float32)
    gray = np.tile(gradient, (64, 1))
    rgb = np.stack((gray, gray * 0.95, gray * 0.85), axis=2)
    metrics = measure(rgb)
    keep, edit = scores(metrics)
    assert 0 <= keep <= 100
    assert 0 <= edit <= 100
    assert metrics.highlights == 100
    assert metrics.shadows == 100


def test_clipped_image_has_low_recovery_metrics():
    rgb = np.ones((40, 40, 3), dtype=np.float32)
    metrics = measure(rgb)
    assert metrics.highlights < 40
    assert metrics.exposure < 20

