from pathlib import Path

from PIL import Image

from raw_photo_curator.catalog import Catalog
from raw_photo_curator.embedding import ColorGridEmbedding
from raw_photo_curator.grouping import build_groups
from raw_photo_curator.metadata import extract_metadata, hamming_distance, perceptual_hash


def record(
    photo_id: str,
    timestamp: str,
    hash_value: str,
    embedding: tuple[float, ...],
) -> dict[str, object]:
    return {
        "id": photo_id,
        "path": f"{photo_id}.arw",
        "metadata": {"capture_time": timestamp, "sequence": int(photo_id)},
        "perceptual_hash": hash_value,
        "embedding": embedding,
        "embedding_version": "test",
    }


def test_extracts_jpeg_metadata_and_stable_perceptual_hash(tmp_path: Path):
    path = tmp_path / "DSC00123.jpg"
    exif = Image.Exif()
    exif[271] = "Sony"
    exif[272] = "ILCE-7CR"
    exif[306] = "2025:07:05 18:04:53"
    image = Image.new("RGB", (32, 24), (30, 80, 140))
    image.save(path, exif=exif)
    metadata = extract_metadata(path)
    assert metadata.camera_make == "Sony"
    assert metadata.camera_model == "ILCE-7CR"
    assert metadata.sequence == 123
    assert metadata.capture_time == "2025-07-05T18:04:53+00:00"
    assert hamming_distance(perceptual_hash(image), perceptual_hash(image)) == 0


def test_groups_nearby_similar_frames_but_not_other_scenes():
    embedding = ColorGridEmbedding().embed(Image.new("RGB", (20, 20), (80, 100, 120)))
    records = [
        record("1", "2025-01-01T12:00:00+00:00", "1111111111111111", embedding),
        record("2", "2025-01-01T12:00:02+00:00", "1111111111111113", embedding),
        record("3", "2025-01-01T12:10:00+00:00", "ffffffffffffffff", embedding),
    ]
    groups = build_groups(records)
    assert len(groups) == 1
    assert groups[0].photo_ids == ("1", "2")
    assert groups[0].type == "duplicate"


def test_manual_group_correction_is_persisted(tmp_path: Path):
    with Catalog(tmp_path / "catalog.sqlite3") as catalog:
        with catalog.connection:
            catalog.connection.executemany(
                """INSERT INTO photos(id, path, size, mtime_ns, format, updated_at)
                VALUES (?, ?, 1, 1, '.arw', 'now')""",
                [("one", "/one.arw"), ("two", "/two.arw"), ("three", "/three.arw")],
            )
        created = catalog.save_manual_partitions(
            [], [["one", "two"], ["three"]], "related"
        )
        assert len(created) == 1
        saved = catalog.groups()
        assert saved[0]["manually_corrected"] is True
        assert [member["id"] for member in saved[0]["members"]] == ["one", "two"]
