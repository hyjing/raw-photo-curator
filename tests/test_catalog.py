import os
import sqlite3
from pathlib import Path

from PIL import Image

from raw_photo_curator.catalog import SCHEMA_VERSION, Catalog
from raw_photo_curator.cli import analyze


def test_analysis_cache_hits_and_invalidates_changed_file(tmp_path: Path):
    photos = tmp_path / "photos"
    output = tmp_path / "output"
    photos.mkdir()
    source = photos / "photo.jpg"
    Image.new("RGB", (48, 32), (30, 90, 150)).save(source)

    first_stats: list[dict[str, int]] = []
    analyze(photos, output, stats=first_stats.append)
    assert first_stats == [
        {"hits": 0, "misses": 1, "failed": 0, "deleted": 0, "cancelled": 0}
    ]
    with sqlite3.connect(output / "catalog.sqlite3") as connection:
        assert connection.execute("SELECT count(*) FROM criterion_results").fetchone()[0] == 17

    warm_stats: list[dict[str, int]] = []
    analyze(photos, output, stats=warm_stats.append)
    assert warm_stats == [
        {"hits": 1, "misses": 0, "failed": 0, "deleted": 0, "cancelled": 0}
    ]

    Image.new("RGB", (48, 32), (180, 40, 20)).save(source)
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    changed_stats: list[dict[str, int]] = []
    analyze(photos, output, stats=changed_stats.append)
    assert changed_stats == [
        {"hits": 0, "misses": 1, "failed": 0, "deleted": 0, "cancelled": 0}
    ]


def test_missing_thumbnail_forces_regeneration(tmp_path: Path):
    photos = tmp_path / "photos"
    output = tmp_path / "output"
    photos.mkdir()
    Image.new("RGB", (48, 32), (30, 90, 150)).save(photos / "photo.jpg")
    result = analyze(photos, output)[0]
    (output / result.thumbnail).unlink()

    stats: list[dict[str, int]] = []
    analyze(photos, output, stats=stats.append)
    assert stats == [
        {"hits": 0, "misses": 1, "failed": 0, "deleted": 0, "cancelled": 0}
    ]
    assert (output / result.thumbnail).is_file()


def test_catalog_migrates_existing_v1_database(tmp_path: Path):
    database = tmp_path / "catalog.sqlite3"
    with Catalog(database):
        pass
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'analysis_jobs'"
        ).fetchone()
