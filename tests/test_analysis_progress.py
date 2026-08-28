from pathlib import Path

from PIL import Image

from raw_photo_curator.cli import analyze


def test_analysis_reports_progress(tmp_path: Path):
    photos = tmp_path / "photos"
    output = tmp_path / "output"
    photos.mkdir()
    for number in range(3):
        Image.new("RGB", (32, 24), (40 + number * 20, 80, 120)).save(
            photos / f"{number}.jpg"
        )
    updates: list[tuple[int, int]] = []
    results = analyze(photos, output, progress=lambda current, total: updates.append((current, total)))
    assert len(results) == 3
    assert updates[0] == (0, 3)
    assert updates[-1] == (3, 3)


def test_analysis_can_cancel_and_resume_from_cache(tmp_path: Path):
    photos = tmp_path / "photos"
    output = tmp_path / "output"
    photos.mkdir()
    for number in range(3):
        Image.new("RGB", (32, 24), (40 + number * 20, 80, 120)).save(
            photos / f"{number}.jpg"
        )
    progress = 0

    def update(current: int, _total: int) -> None:
        nonlocal progress
        progress = current

    stats: list[dict[str, int]] = []
    partial = analyze(
        photos,
        output,
        progress=update,
        stats=stats.append,
        cancelled=lambda: progress >= 1,
    )
    assert len(partial) == 1
    assert stats[0]["cancelled"] == 1

    resumed_stats: list[dict[str, int]] = []
    resumed = analyze(photos, output, stats=resumed_stats.append)
    assert len(resumed) == 3
    assert resumed_stats[0]["hits"] == 1
    assert resumed_stats[0]["misses"] == 2
