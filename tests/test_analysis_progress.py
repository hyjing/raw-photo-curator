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
