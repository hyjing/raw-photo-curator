import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .grouping import SimilarityGroup
from .metadata import PhotoMetadata
from .models import Metrics, Result

ANALYZER_ID = "builtin.objective"
ANALYZER_VERSION = "3"
SAMPLE_SIZE = 64 * 1024
SCHEMA_VERSION = 3


def _now() -> str:
    return datetime.now(UTC).isoformat()


def fingerprint(path: Path) -> str:
    """Hash stable samples instead of reading an entire multi-megabyte RAW file."""
    size = path.stat().st_size
    digest = hashlib.sha256()
    digest.update(size.to_bytes(8, "big"))
    with path.open("rb") as source:
        offsets = {0, max(0, size // 2 - SAMPLE_SIZE // 2), max(0, size - SAMPLE_SIZE)}
        for offset in sorted(offsets):
            source.seek(offset)
            digest.update(offset.to_bytes(8, "big"))
            digest.update(source.read(SAMPLE_SIZE))
    return digest.hexdigest()


class Catalog:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"catalog schema {version} is newer than supported {SCHEMA_VERSION}"
            )
        if version < 1:
            self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS photos (
                id TEXT PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                format TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS analysis_cache (
                photo_id TEXT NOT NULL REFERENCES photos(id),
                analyzer_id TEXT NOT NULL,
                analyzer_version TEXT NOT NULL,
                keep_score REAL NOT NULL,
                edit_score REAL NOT NULL,
                metrics_json TEXT NOT NULL,
                notes_json TEXT NOT NULL,
                thumbnail TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (photo_id, analyzer_id, analyzer_version)
            );
            CREATE INDEX IF NOT EXISTS photos_path_state
                ON photos(path, size, mtime_ns);
            """
            )
            self.connection.execute("PRAGMA user_version = 1")
            version = 1
        if version < 2:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS analysis_jobs (
                    id TEXT PRIMARY KEY,
                    folder TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current INTEGER NOT NULL DEFAULT 0,
                    total INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self.connection.execute("PRAGMA user_version = 2")
            version = 2
        if version < 3:
            self.connection.execute("ALTER TABLE photos ADD COLUMN metadata_json TEXT")
            self.connection.execute("ALTER TABLE photos ADD COLUMN perceptual_hash TEXT")
            self.connection.execute("ALTER TABLE photos ADD COLUMN embedding_json TEXT")
            self.connection.execute("ALTER TABLE photos ADD COLUMN embedding_version TEXT")
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS similarity_groups (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    manually_corrected INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS group_members (
                    group_id TEXT NOT NULL REFERENCES similarity_groups(id) ON DELETE CASCADE,
                    photo_id TEXT NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    PRIMARY KEY (group_id, photo_id)
                );
                """
            )
            self.connection.execute("PRAGMA user_version = 3")
        self.connection.commit()

    def prune_missing(self) -> int:
        rows = self.connection.execute("SELECT id, path FROM photos").fetchall()
        missing = [(row["id"],) for row in rows if not Path(row["path"]).is_file()]
        with self.connection:
            self.connection.executemany(
                "DELETE FROM analysis_cache WHERE photo_id = ?", missing
            )
            self.connection.executemany("DELETE FROM photos WHERE id = ?", missing)
        return len(missing)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def cached(self, path: Path, output: Path) -> Result | None:
        stat = path.stat()
        row = self.connection.execute(
            """SELECT c.* FROM photos p JOIN analysis_cache c ON c.photo_id = p.id
            WHERE p.path = ? AND p.size = ? AND p.mtime_ns = ?
              AND c.analyzer_id = ? AND c.analyzer_version = ?""",
            (str(path), stat.st_size, stat.st_mtime_ns, ANALYZER_ID, ANALYZER_VERSION),
        ).fetchone()
        if not row or not (output / row["thumbnail"]).is_file():
            return None
        return Result(
            path=path,
            keep_score=row["keep_score"],
            edit_score=row["edit_score"],
            metrics=Metrics(**json.loads(row["metrics_json"])),
            notes=tuple(json.loads(row["notes_json"])),
            thumbnail=row["thumbnail"],
        )

    def store(
        self,
        result: Result,
        metadata: PhotoMetadata | None = None,
        perceptual_hash: str | None = None,
        embedding: tuple[float, ...] | None = None,
        embedding_version: str | None = None,
    ) -> None:
        path = result.path
        stat = path.stat()
        photo_id = fingerprint(path)
        now = _now()
        with self.connection:
            # A path can legitimately point at a replaced file; remove its stale identity first.
            old = self.connection.execute(
                "SELECT id FROM photos WHERE path = ?", (str(path),)
            ).fetchone()
            if old and old["id"] != photo_id:
                self.connection.execute("DELETE FROM analysis_cache WHERE photo_id = ?", (old["id"],))
                self.connection.execute("DELETE FROM photos WHERE id = ?", (old["id"],))
            self.connection.execute(
                """INSERT INTO photos(id, path, size, mtime_ns, format, updated_at,
                metadata_json, perceptual_hash, embedding_json, embedding_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET path=excluded.path, size=excluded.size,
                mtime_ns=excluded.mtime_ns, format=excluded.format, updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json, perceptual_hash=excluded.perceptual_hash,
                embedding_json=excluded.embedding_json,
                embedding_version=excluded.embedding_version""",
                (
                    photo_id,
                    str(path),
                    stat.st_size,
                    stat.st_mtime_ns,
                    path.suffix.lower(),
                    now,
                    json.dumps(metadata.to_dict()) if metadata else None,
                    perceptual_hash,
                    json.dumps(embedding) if embedding else None,
                    embedding_version,
                ),
            )
            self.connection.execute(
                """INSERT INTO analysis_cache(photo_id, analyzer_id, analyzer_version,
                keep_score, edit_score, metrics_json, notes_json, thumbnail, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(photo_id, analyzer_id, analyzer_version) DO UPDATE SET
                keep_score=excluded.keep_score, edit_score=excluded.edit_score,
                metrics_json=excluded.metrics_json, notes_json=excluded.notes_json,
                thumbnail=excluded.thumbnail, updated_at=excluded.updated_at""",
                (
                    photo_id,
                    ANALYZER_ID,
                    ANALYZER_VERSION,
                    result.keep_score,
                    result.edit_score,
                    json.dumps(asdict(result.metrics)),
                    json.dumps(result.notes, ensure_ascii=False),
                    result.thumbnail,
                    now,
                ),
            )

    def photo_records(self) -> list[dict[str, object]]:
        records = []
        for row in self.connection.execute(
            """SELECT id, path, metadata_json, perceptual_hash,
            embedding_json, embedding_version FROM photos"""
        ):
            records.append(
                {
                    "id": row["id"],
                    "path": row["path"],
                    "metadata": json.loads(row["metadata_json"] or "{}"),
                    "perceptual_hash": row["perceptual_hash"],
                    "embedding": tuple(json.loads(row["embedding_json"]))
                    if row["embedding_json"]
                    else None,
                    "embedding_version": row["embedding_version"],
                }
            )
        return records

    def replace_automatic_groups(self, groups: list[SimilarityGroup]) -> None:
        now = _now()
        with self.connection:
            automatic = self.connection.execute(
                "SELECT id FROM similarity_groups WHERE manually_corrected = 0"
            ).fetchall()
            self.connection.executemany(
                "DELETE FROM similarity_groups WHERE id = ?",
                [(row["id"],) for row in automatic],
            )
            for group in groups:
                self.connection.execute(
                    """INSERT INTO similarity_groups(id, type, confidence, updated_at)
                    VALUES (?, ?, ?, ?)""",
                    (group.id, group.type, group.confidence, now),
                )
                self.connection.executemany(
                    """INSERT INTO group_members(group_id, photo_id, position)
                    VALUES (?, ?, ?)""",
                    [
                        (group.id, photo_id, position)
                        for position, photo_id in enumerate(group.photo_ids)
                    ],
                )

    def groups(self) -> list[dict[str, object]]:
        output = []
        for row in self.connection.execute(
            "SELECT * FROM similarity_groups ORDER BY updated_at DESC"
        ):
            members = self.connection.execute(
                """SELECT p.id, p.path, gm.position,
                (SELECT thumbnail FROM analysis_cache ac WHERE ac.photo_id = p.id
                 ORDER BY ac.updated_at DESC LIMIT 1) AS thumbnail
                FROM group_members gm
                JOIN photos p ON p.id = gm.photo_id WHERE gm.group_id = ?
                ORDER BY gm.position""",
                (row["id"],),
            ).fetchall()
            output.append(
                {
                    "id": row["id"],
                    "type": row["type"],
                    "confidence": row["confidence"],
                    "manually_corrected": bool(row["manually_corrected"]),
                    "members": [dict(member) for member in members],
                }
            )
        return output

    def save_manual_partitions(
        self, source_group_ids: list[str], partitions: list[list[str]], group_type: str
    ) -> list[str]:
        now = _now()
        created = []
        with self.connection:
            self.connection.executemany(
                "DELETE FROM similarity_groups WHERE id = ?",
                [(group_id,) for group_id in source_group_ids],
            )
            for members in partitions:
                if len(members) < 2:
                    continue
                group_id = hashlib.sha256(
                    ("manual\0" + "\0".join(sorted(members))).encode()
                ).hexdigest()[:20]
                self.connection.execute(
                    """INSERT INTO similarity_groups
                    (id, type, confidence, manually_corrected, updated_at)
                    VALUES (?, ?, 1.0, 1, ?)""",
                    (group_id, group_type, now),
                )
                self.connection.executemany(
                    "INSERT INTO group_members(group_id, photo_id, position) VALUES (?, ?, ?)",
                    [
                        (group_id, photo_id, position)
                        for position, photo_id in enumerate(members)
                    ],
                )
                created.append(group_id)
        return created
