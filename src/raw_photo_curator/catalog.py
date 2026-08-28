import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .models import Metrics, Result

ANALYZER_ID = "builtin.objective"
ANALYZER_VERSION = "2"
SAMPLE_SIZE = 64 * 1024


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
        self.connection.execute("PRAGMA journal_mode=WAL")
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
            PRAGMA user_version = 1;
            """
        )

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

    def store(self, result: Result) -> None:
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
                """INSERT INTO photos(id, path, size, mtime_ns, format, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET path=excluded.path, size=excluded.size,
                mtime_ns=excluded.mtime_ns, format=excluded.format, updated_at=excluded.updated_at""",
                (photo_id, str(path), stat.st_size, stat.st_mtime_ns, path.suffix.lower(), now),
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
