import json
from pathlib import Path

from raw_photo_curator.models import Metrics, Result
from raw_photo_curator.server import SessionState, _connect, _refresh_candidates


def make_result(number: int) -> Result:
    value = 90 - number
    metrics = Metrics(*(value for _ in range(9)))
    path = Path(f"photo-{number}.arw")
    return Result(path, value, value, metrics, (), f"photo-{number}.jpg")


def save_choice(database: Path, path: str, choice: str) -> None:
    with _connect(database) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO feedback VALUES (?, ?, ?, ?, ?, ?)",
            (path, choice, None, json.dumps([]), "", "now"),
        )


def test_reject_replaces_candidate_and_five_keeps_start_new_round(tmp_path: Path):
    database = tmp_path / "feedback.sqlite3"
    results = [make_result(number) for number in range(12)]
    state = SessionState(results, tmp_path, tmp_path, 5, object(), candidate_paths=[])
    _refresh_candidates(state, database)
    first_batch = list(state.candidate_paths or [])
    assert len(first_batch) == 5

    save_choice(database, first_batch[0], "reject")
    _refresh_candidates(state, database)
    assert first_batch[0] not in (state.candidate_paths or [])
    assert len(state.candidate_paths or []) == 5

    kept_batch = list(state.candidate_paths or [])
    for path in kept_batch:
        save_choice(database, path, "keep")
    _refresh_candidates(state, database)
    assert state.round_number == 2
    assert not set(kept_batch) & set(state.candidate_paths or [])
