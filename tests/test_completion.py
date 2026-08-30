from datetime import UTC, datetime
from pathlib import Path

from raw_photo_curator.models import Metrics, Result
from raw_photo_curator.server import _completion_payload, _connect


def _result(path: Path, score: float, value: float) -> Result:
    return Result(
        path=path,
        keep_score=score,
        edit_score=score,
        metrics=Metrics(
            sharpness=value,
            exposure=value + 1,
            highlights=value + 2,
            shadows=value + 3,
            contrast=value + 4,
            noise=value + 5,
            color=value + 6,
            white_balance=value + 7,
            composition=value + 8,
        ),
        notes=(),
        thumbnail=str(path.with_suffix(".jpg")),
    )


def test_completion_payload_summarizes_selection(tmp_path: Path):
    database = tmp_path / "feedback.sqlite3"
    results = [_result(tmp_path / "one.ARW", 80, 60), _result(tmp_path / "two.ARW", 70, 40)]
    with _connect(database) as connection:
        connection.execute(
            "INSERT INTO feedback(path, choice, updated_at) VALUES (?, 'keep', ?)",
            (str(results[0].path), datetime.now(UTC).isoformat()),
        )
    payload = _completion_payload(database, results)
    assert payload["selected_count"] == 1
    assert payload["reviewed_count"] == 1
    assert payload["completion_percent"] == 50
    assert payload["profile_name"] == "Travel"
    assert payload["average_metrics"]["composition"] == 68
    assert payload["selected_items"][0]["name"] == "one.ARW"
    assert payload["model"]["ready"] is False
