import json
import mimetypes
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

UTC = timezone.utc
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from threading import Event, Lock, Thread
from urllib.parse import parse_qs, urlparse

from PIL import Image

from .active_selection import select_active_candidates
from .catalog import Catalog
from .models import Result
from .personal_evaluation import evaluate_personalization
from .plugins import default_registry
from .profiles import BUILTIN_PROFILES, METRIC_IDS, Profile, hard_rule_reasons, weighted_score
from .ranker import PreferenceModel, contributions, learned_weight, predict, train_pairwise
from .recommendation import recommendation_scores
from .workflow import apply_actions, export_selection, plan_file_actions, plan_xmp_actions

CHOICES = {"keep", "edit", "reject", "maybe", None}


@dataclass
class SessionState:
    results: list[Result]
    output: Path
    folder: Path | None
    limit: int
    analyzer: object
    progress_current: int = 0
    progress_total: int = 0
    progress_running: bool = False
    progress_stage: str = "idle"
    candidate_paths: list[str] | None = None
    round_number: int = 1
    cache_hits: int = 0
    cache_misses: int = 0
    cache_failed: int = 0
    cache_deleted: int = 0
    progress_error: str = ""
    cancel_event: Event = field(default_factory=Event)
    job_lock: Lock = field(default_factory=Lock)


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS feedback (
        path TEXT PRIMARY KEY,
        choice TEXT,
        rating INTEGER,
        tags TEXT NOT NULL DEFAULT '[]',
        note TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS rotations (
        path TEXT PRIMARY KEY,
        degrees INTEGER NOT NULL DEFAULT 0
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS profiles (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, weights TEXT NOT NULL,
        hard_rules TEXT NOT NULL, enabled_plugins TEXT NOT NULL,
        is_builtin INTEGER NOT NULL DEFAULT 0
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS group_feedback (
        group_id TEXT NOT NULL, photo_id TEXT NOT NULL,
        decision TEXT NOT NULL, reason_criteria TEXT NOT NULL DEFAULT '[]',
        updated_at TEXT NOT NULL, PRIMARY KEY(group_id, photo_id)
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS profile_feedback (
        profile_id TEXT NOT NULL, path TEXT NOT NULL, choice TEXT,
        rating INTEGER, tags TEXT NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL, PRIMARY KEY(profile_id, path)
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS preference_models (
        profile_id TEXT PRIMARY KEY, model_json TEXT NOT NULL, updated_at TEXT NOT NULL
        )"""
    )
    for profile in BUILTIN_PROFILES:
        connection.execute(
            """INSERT INTO profiles
            (id, name, weights, hard_rules, enabled_plugins, is_builtin)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
            name=CASE WHEN excluded.is_builtin=1 THEN excluded.name ELSE profiles.name END,
            weights=CASE WHEN excluded.is_builtin=1 THEN excluded.weights ELSE profiles.weights END,
            hard_rules=CASE WHEN excluded.is_builtin=1 THEN excluded.hard_rules ELSE profiles.hard_rules END,
            enabled_plugins=CASE WHEN excluded.is_builtin=1 THEN excluded.enabled_plugins ELSE profiles.enabled_plugins END,
            is_builtin=excluded.is_builtin""",
            (
                profile.id,
                profile.name,
                json.dumps(profile.weights),
                json.dumps(profile.hard_rules),
                json.dumps(profile.enabled_plugins),
                int(profile.id != "custom"),
            ),
        )
    custom_defaults = next(profile for profile in BUILTIN_PROFILES if profile.id == "custom")
    custom_row = connection.execute(
        "SELECT weights FROM profiles WHERE id = 'custom'"
    ).fetchone()
    custom_weights = json.loads(custom_row["weights"])
    for criterion_id, weight in custom_defaults.weights.items():
        custom_weights.setdefault(criterion_id, weight)
    connection.execute(
        "UPDATE profiles SET weights = ?, is_builtin = 0 WHERE id = 'custom'",
        (json.dumps(custom_weights),),
    )
    connection.execute(
        "INSERT OR IGNORE INTO app_settings(key, value) VALUES ('active_profile', 'travel')"
    )
    connection.execute(
        "INSERT OR IGNORE INTO app_settings(key, value) VALUES ('anonymous_statistics', 'off')"
    )
    migrated = connection.execute(
        "SELECT value FROM app_settings WHERE key = 'profile_feedback_migrated'"
    ).fetchone()
    if not migrated:
        active_profile = connection.execute(
            "SELECT value FROM app_settings WHERE key = 'active_profile'"
        ).fetchone()["value"]
        connection.execute(
            """INSERT OR IGNORE INTO profile_feedback
            (profile_id, path, choice, rating, tags, note, updated_at)
            SELECT ?, path, choice, rating, tags, note, updated_at FROM feedback""",
            (active_profile,),
        )
        connection.execute(
            "INSERT INTO app_settings(key, value) VALUES ('profile_feedback_migrated', '1')"
        )
    connection.commit()
    return connection


def _profiles(database: Path) -> list[Profile]:
    with _connect(database) as connection:
        return [
            Profile(
                row["id"],
                row["name"],
                json.loads(row["weights"]),
                json.loads(row["hard_rules"]),
                tuple(json.loads(row["enabled_plugins"])),
            )
            for row in connection.execute("SELECT * FROM profiles ORDER BY is_builtin DESC, name")
        ]


def _active_profile(database: Path) -> Profile:
    with _connect(database) as connection:
        active = connection.execute(
            "SELECT value FROM app_settings WHERE key = 'active_profile'"
        ).fetchone()["value"]
    return next((profile for profile in _profiles(database) if profile.id == active), _profiles(database)[0])


def _profile_priors(
    results: list[Result], profile: Profile, catalog_path: Path
) -> dict[str, float]:
    if not catalog_path.is_file():
        return {str(result.path): weighted_score(result, profile) for result in results}
    with Catalog(catalog_path) as catalog:
        return {
            str(result.path): weighted_score(
                result, profile, catalog.criteria_for_path(str(result.path))
            )
            for result in results
        }


def _preference_contexts(catalog_path: Path) -> dict[str, dict]:
    if not catalog_path.is_file():
        return {}
    with Catalog(catalog_path) as catalog:
        return catalog.preference_contexts()


def _load_model(database: Path, profile_id: str) -> PreferenceModel | None:
    with _connect(database) as connection:
        row = connection.execute(
            "SELECT model_json FROM preference_models WHERE profile_id = ?", (profile_id,)
        ).fetchone()
    if not row:
        return None
    try:
        return PreferenceModel.from_json(row["model_json"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _train_model(
    database: Path, results: list[Result], profile: Profile, catalog_path: Path
) -> PreferenceModel | None:
    feedback = _read_feedback(database, profile.id)
    model = train_pairwise(
        results, feedback, _preference_contexts(catalog_path), profile.id
    )
    with _connect(database) as connection:
        if model:
            connection.execute(
                """INSERT INTO preference_models(profile_id, model_json, updated_at)
                VALUES (?, ?, ?) ON CONFLICT(profile_id) DO UPDATE SET
                model_json=excluded.model_json, updated_at=excluded.updated_at""",
                (profile.id, model.to_json(), model.trained_at),
            )
        else:
            connection.execute(
                "DELETE FROM preference_models WHERE profile_id = ?", (profile.id,)
            )
    return model


def _ranking(
    results: list[Result], database: Path, profile: Profile, catalog_path: Path
) -> tuple[
    dict[str, float], dict[str, float], PreferenceModel | None,
    dict[str, dict], dict[str, float] | None,
]:
    feedback = _read_feedback(database, profile.id)
    priors = _profile_priors(results, profile, catalog_path)
    model = _load_model(database, profile.id)
    contexts = _preference_contexts(catalog_path) if model else {}
    learned = (
        {str(item.path): predict(model, item, contexts.get(str(item.path))) for item in results}
        if model else None
    )
    scores = recommendation_scores(
        results, feedback, priors, learned, learned_weight(model)
    )
    return scores, priors, model, contexts, learned


def _photo_payload(results: list[Result], database: Path) -> list[dict]:
    saved = _read_feedback(database)
    profile = _active_profile(database)
    priors = {str(result.path): weighted_score(result, profile) for result in results}
    personalized = recommendation_scores(results, saved, priors)
    payload = []
    for index, result in enumerate(results):
        item = result.to_dict()
        item["id"] = index
        thumbnail_path = Path(result.thumbnail).name
        item["thumbnail"] = f"/thumbnails/{thumbnail_path}?v={Path(result.path).stat().st_mtime_ns}"
        feedback = saved.get(str(result.path))
        if feedback:
            feedback["tags"] = json.loads(feedback["tags"])
        item["feedback"] = feedback
        item["recommendation_score"] = personalized[str(result.path)]
        payload.append(item)
    return payload


def _read_feedback(database: Path, profile_id: str | None = None) -> dict[str, dict]:
    with _connect(database) as connection:
        if profile_id:
            rows = connection.execute(
                "SELECT path, choice, rating, tags, note, updated_at FROM profile_feedback WHERE profile_id = ?",
                (profile_id,),
            ).fetchall()
            if rows:
                return {row["path"]: dict(row) for row in rows}
            reset = connection.execute(
                "SELECT 1 FROM app_settings WHERE key = ?",
                (f"profile_feedback_reset:{profile_id}",),
            ).fetchone()
            if reset:
                return {}
        return {row["path"]: dict(row) for row in connection.execute("SELECT * FROM feedback")}


def _refresh_candidates(state: SessionState, database: Path) -> None:
    profile = _active_profile(database)
    feedback = _read_feedback(database, profile.id)
    scores, _, _, contexts, learned = _ranking(
        state.results, database, profile, state.output / "catalog.sqlite3"
    )
    current = set(state.candidate_paths or [])
    kept_current = [
        path for path in (state.candidate_paths or [])
        if feedback.get(path, {}).get("choice") == "keep"
    ]
    active_unreviewed = [
        path for path in (state.candidate_paths or []) if path not in feedback
    ]
    if len(kept_current) >= 5:
        current = set()
        kept_current = []
        active_unreviewed = []
        state.round_number += 1
    excluded = set(feedback) | current
    available = []
    for result in state.results:
        path = str(result.path)
        criteria = list(contexts.get(path, {}).get("criteria", {}).values())
        if path not in excluded and not hard_rule_reasons(result, profile, criteria):
            available.append(result)
    retained = kept_current + active_unreviewed
    needed = 5 - len(retained)
    selected = select_active_candidates(
        available, scores, learned, contexts, needed
    )
    state.candidate_paths = retained + [str(result.path) for result in selected]


def _candidate_payload(state: SessionState, database: Path) -> list[dict]:
    with _connect(database) as connection:
        rotations = {row["path"]: row["degrees"] for row in connection.execute("SELECT * FROM rotations")}
    profile = _active_profile(database)
    feedback = _read_feedback(database, profile.id)
    scores, priors, model, contexts, _ = _ranking(
        state.results, database, profile, state.output / "catalog.sqlite3"
    )
    by_path = {str(result.path): result for result in state.results}
    output = []
    catalog_path = state.output / "catalog.sqlite3"
    catalog = Catalog(catalog_path) if catalog_path.is_file() else None
    try:
        for path in state.candidate_paths or []:
            result = by_path.get(path)
            if not result:
                continue
            item = result.to_dict()
            rotation = rotations.get(path, 0)
            item["thumbnail"] = f"/thumbnails/{Path(result.thumbnail).name}?v={result.path.stat().st_mtime_ns}&r={rotation}"
            item["recommendation_score"] = scores[path]
            item["profile_score"] = priors[path]
            item["profile_id"] = profile.id
            item["recommendation_confidence"] = round(learned_weight(model), 3)
            item["score_contributions"] = (
                contributions(model, result, contexts.get(path)) if model else []
            )
            item["criteria"] = catalog.criteria_for_path(path) if catalog else []
            item["kept"] = feedback.get(path, {}).get("choice") == "keep"
            item["rotation"] = rotation
            output.append(item)
    finally:
        if catalog:
            catalog.close()
    return output


def _summary(database: Path, results: list[Result]) -> dict[str, int]:
    counts = {"keep": 0, "edit": 0, "reject": 0, "maybe": 0, "reviewed": 0}
    active_paths = {str(result.path) for result in results}
    with _connect(database) as connection:
        for row in connection.execute("SELECT path, choice FROM feedback"):
            if row["path"] in active_paths:
                if row["choice"] in counts:
                    counts[row["choice"]] += 1
                counts["reviewed"] += 1
    return counts


def _completion_payload(database: Path, results: list[Result]) -> dict[str, object]:
    profile = _active_profile(database)
    model = _load_model(database, profile.id)
    feedback = _read_feedback(database, profile.id)
    by_path = {str(result.path): result for result in results}
    available = set(by_path)
    selected = [
        path
        for path, item in feedback.items()
        if path in available and item.get("choice") in {"keep", "edit"}
    ]
    rejected = [
        path
        for path, item in feedback.items()
        if path in available and item.get("choice") == "reject"
    ]
    selected_results = [by_path[path] for path in selected]
    metric_names = (
        "sharpness", "exposure", "highlights", "shadows", "contrast",
        "color", "white_balance", "composition",
    )
    averages = {
        name: round(
            sum(getattr(result.metrics, name) for result in selected_results)
            / len(selected_results),
            1,
        )
        for name in metric_names
    } if selected_results else {}
    reviewed_count = len(selected) + len(rejected)
    return {
        "profile_id": profile.id,
        "profile_name": profile.name,
        "selected": selected,
        "selected_count": len(selected),
        "rejected_count": len(rejected),
        "reviewed_count": reviewed_count,
        "unreviewed_count": max(0, len(results) - reviewed_count),
        "completion_percent": round(reviewed_count / len(results) * 100) if results else 0,
        "average_metrics": averages,
        "selected_items": [
            {
                "path": str(result.path),
                "name": result.path.name,
                "thumbnail": f"/thumbnails/{Path(result.thumbnail).name}",
                "keep_score": result.keep_score,
            }
            for result in sorted(selected_results, key=lambda item: item.keep_score, reverse=True)[:12]
        ],
        "model": {
            "ready": model is not None,
            "training_count": model.training_count if model else reviewed_count,
            "influence": round(learned_weight(model) * 100) if model else 0,
        },
    }


def _choose_macos_folder(prompt: str) -> Path | None:
    if sys.platform != "darwin":
        raise RuntimeError("native folder picker currently requires macOS")
    escaped = prompt.replace('"', "'")
    selection = subprocess.run(
        ["osascript", "-e", f'POSIX path of (choose folder with prompt "{escaped}")'],
        capture_output=True,
        text=True,
        check=False,
    )
    if selection.returncode != 0 or not selection.stdout.strip():
        return None
    folder = Path(selection.stdout.strip()).expanduser().resolve()
    return folder if folder.is_dir() else None


def make_handler(state: SessionState, database: Path):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            route = parsed.path
            if route == "/":
                body = APP_HTML.encode()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif route == "/api/photos":
                self._json({
                    "photos": _photo_payload(state.results, database),
                    "summary": _summary(database, state.results),
                    "folder": str(state.folder) if state.folder else "",
                })
            elif route == "/api/progress":
                self._json({
                    "current": state.progress_current,
                    "total": state.progress_total,
                    "running": state.progress_running,
                    "stage": state.progress_stage,
                    "cache_hits": state.cache_hits,
                    "cache_misses": state.cache_misses,
                    "failed": state.cache_failed,
                    "deleted": state.cache_deleted,
                    "error": state.progress_error,
                })
            elif route == "/api/candidates":
                self._json({
                    "candidates": _candidate_payload(state, database),
                    "folder": str(state.folder) if state.folder else "",
                    "round": state.round_number,
                    "summary": _summary(database, state.results),
                    "active_profile": _active_profile(database).id,
                })
            elif route == "/api/completion":
                self._json(_completion_payload(database, state.results))
            elif route == "/api/profiles":
                active = _active_profile(database)
                self._json({
                    "profiles": [profile.to_dict() for profile in _profiles(database)],
                    "active_profile": active.id,
                })
            elif route == "/api/groups":
                with Catalog(state.output / "catalog.sqlite3") as catalog:
                    groups = catalog.groups()
                with _connect(database) as connection:
                    group_feedback = {
                        (row["group_id"], row["photo_id"]): row["decision"]
                        for row in connection.execute("SELECT * FROM group_feedback")
                    }
                    rotations = {
                        row["path"]: row["degrees"]
                        for row in connection.execute("SELECT * FROM rotations")
                    }
                thumbnails = {
                    str(result.path): f"/thumbnails/{Path(result.thumbnail).name}"
                    for result in state.results
                }
                for group in groups:
                    for member in group["members"]:
                        thumbnail = thumbnails.get(member["path"])
                        if not thumbnail and member.get("thumbnail"):
                            thumbnail = f"/thumbnails/{Path(member['thumbnail']).name}"
                        rotation = rotations.get(str(member["path"]), 0)
                        member["thumbnail"] = (
                            f"{thumbnail}?r={rotation}" if thumbnail else None
                        )
                        member["rotation"] = rotation
                        member["decision"] = group_feedback.get(
                            (group["id"], member["id"])
                        )
                self._json({"groups": groups})
            elif route == "/api/plugins":
                with Catalog(state.output / "catalog.sqlite3") as catalog:
                    configured = catalog.plugin_settings()
                enabled = (
                    {plugin_id for plugin_id, value in configured.items() if value}
                    if configured
                    else None
                )
                self._json({"plugins": default_registry(enabled).describe()})
            elif route == "/api/model":
                profile = _active_profile(database)
                model = _load_model(database, profile.id)
                self._json({
                    "profile_id": profile.id,
                    "ready": model is not None,
                    "learned_weight": learned_weight(model),
                    "model": json.loads(model.to_json()) if model else None,
                })
            elif route == "/api/evaluation":
                profile = _active_profile(database)
                feedback = _read_feedback(database, profile.id)
                contexts = _preference_contexts(state.output / "catalog.sqlite3")
                priors = _profile_priors(
                    state.results, profile, state.output / "catalog.sqlite3"
                )
                self._json(evaluate_personalization(
                    state.results, feedback, contexts, priors, profile.id
                ))
            elif route.startswith("/thumbnails/"):
                name = Path(route).name
                candidate = state.output / "thumbnails" / name
                if candidate.is_file() and candidate.parent.resolve() == (state.output / "thumbnails").resolve():
                    body = candidate.read_bytes()
                    rotation = int(parse_qs(parsed.query).get("r", ["0"])[0]) % 360
                    if rotation:
                        with Image.open(BytesIO(body)) as image:
                            rotated = image.rotate(-rotation, expand=True)
                            buffer = BytesIO()
                            rotated.save(buffer, "JPEG", quality=86, optimize=True)
                            body = buffer.getvalue()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "image/jpeg")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(HTTPStatus.NOT_FOUND)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            route = urlparse(self.path).path
            if route == "/api/folder":
                self._change_folder()
                return
            if route == "/api/folder-picker":
                self._pick_folder()
                return
            if route == "/api/finalize":
                self._finalize_selection()
                return
            if route == "/api/more":
                self._load_more()
                return
            if route == "/api/recommendations":
                self._build_recommendations()
                return
            if route == "/api/cancel":
                state.cancel_event.set()
                self._json({"ok": True, "running": state.progress_running})
                return
            if route == "/api/profile":
                self._change_profile()
                return
            if route == "/api/profile/custom":
                self._update_custom_profile()
                return
            if route == "/api/groups/correct":
                self._correct_groups()
                return
            if route == "/api/group-feedback":
                self._save_group_feedback()
                return
            if route == "/api/plugin":
                self._change_plugin()
                return
            if route == "/api/rotation":
                self._rotate_photo()
                return
            if route == "/api/model/reset":
                profile = _active_profile(database)
                with _connect(database) as connection:
                    connection.execute(
                        "DELETE FROM preference_models WHERE profile_id = ?", (profile.id,)
                    )
                    connection.execute(
                        "DELETE FROM profile_feedback WHERE profile_id = ?", (profile.id,)
                    )
                    connection.execute(
                        "INSERT OR REPLACE INTO app_settings(key, value) VALUES (?, '1')",
                        (f"profile_feedback_reset:{profile.id}",),
                    )
                state.candidate_paths = []
                _refresh_candidates(state, database)
                self._json({"ok": True, "profile_id": profile.id})
                return
            if route == "/api/model/import":
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 10_000_000:
                        raise ValueError("invalid body size")
                    data = json.loads(self.rfile.read(size))
                    model_data = data.get("model", data)
                    model = PreferenceModel.from_json(json.dumps(model_data))
                    profile = _active_profile(database)
                    if model.profile_id != profile.id:
                        raise ValueError("model belongs to another profile")
                    with _connect(database) as connection:
                        connection.execute(
                            """INSERT INTO preference_models(profile_id, model_json, updated_at)
                            VALUES (?, ?, ?) ON CONFLICT(profile_id) DO UPDATE SET
                            model_json=excluded.model_json, updated_at=excluded.updated_at""",
                            (profile.id, model.to_json(), model.trained_at),
                        )
                    state.candidate_paths = []
                    _refresh_candidates(state, database)
                    self._json({"ok": True, "profile_id": profile.id})
                except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                    self._json(
                        {"ok": False, "error": "模型格式或 Profile 不兼容"},
                        HTTPStatus.BAD_REQUEST,
                    )
                return
            if route != "/api/feedback":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65_536:
                    raise ValueError("invalid body size")
                data = json.loads(self.rfile.read(size))
                if "path" in data:
                    requested_path = str(data["path"])
                    result = next(item for item in state.results if str(item.path) == requested_path)
                else:
                    result = state.results[int(data["id"])]
                choice = data.get("choice")
                rating = data.get("rating")
                tags = data.get("tags", [])
                note = str(data.get("note", "")).strip()[:300]
                if choice not in CHOICES or rating not in {None, 1, 2, 3, 4, 5}:
                    raise ValueError("invalid feedback")
                if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                    raise ValueError("invalid tags")
                now = datetime.now(UTC).isoformat()
                profile_id = _active_profile(database).id
                with _connect(database) as connection:
                    connection.execute(
                        "DELETE FROM app_settings WHERE key = ?",
                        (f"profile_feedback_reset:{profile_id}",),
                    )
                    connection.execute(
                        """INSERT INTO feedback(path, choice, rating, tags, note, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(path) DO UPDATE SET choice=excluded.choice,
                        rating=excluded.rating, tags=excluded.tags, note=excluded.note,
                        updated_at=excluded.updated_at""",
                        (str(result.path), choice, rating, json.dumps(tags[:8]), note, now),
                    )
                    connection.execute(
                        """INSERT INTO profile_feedback
                        (profile_id, path, choice, rating, tags, note, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(profile_id, path) DO UPDATE SET choice=excluded.choice,
                        rating=excluded.rating, tags=excluded.tags, note=excluded.note,
                        updated_at=excluded.updated_at""",
                        (profile_id, str(result.path), choice, rating, json.dumps(tags[:8]), note, now),
                    )
                profile = _active_profile(database)
                _train_model(database, state.results, profile, state.output / "catalog.sqlite3")
                _refresh_candidates(state, database)
                self._json({
                    "ok": True,
                    "summary": _summary(database, state.results),
                    "round": state.round_number,
                    "candidates": _candidate_payload(state, database),
                })
            except (ValueError, KeyError, IndexError, StopIteration, json.JSONDecodeError):
                self._json({"ok": False, "error": "invalid feedback"}, HTTPStatus.BAD_REQUEST)

        def _change_folder(self) -> None:
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16_384:
                    raise ValueError("invalid body size")
                data = json.loads(self.rfile.read(size))
                folder = Path(str(data["folder"])).expanduser().resolve()
                if not folder.is_dir():
                    self._json({"ok": False, "error": "文件夹不存在"}, HTTPStatus.BAD_REQUEST)
                    return
                new_results = state.analyzer(folder, state.output, state.limit)
                if not new_results:
                    self._json({"ok": False, "error": "没有找到支持的照片"}, HTTPStatus.BAD_REQUEST)
                    return
                state.results = new_results
                state.folder = folder
                self._json({"ok": True, "count": len(new_results), "folder": str(folder)})
            except (ValueError, KeyError, json.JSONDecodeError):
                self._json({"ok": False, "error": "无效的文件夹路径"}, HTTPStatus.BAD_REQUEST)

        def _pick_folder(self) -> None:
            try:
                size = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(min(size, 16_384))) if size else {}
                purpose = str(data.get("purpose", "photos"))
                prompt = (
                    "选择保存保留照片的文件夹"
                    if purpose == "export"
                    else "选择包含 RAW / JPG 照片的文件夹"
                )
                selected = _choose_macos_folder(prompt)
                self._json(
                    {"ok": selected is not None, "cancelled": selected is None,
                     "folder": str(selected) if selected else None}
                )
            except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
                self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

        def _finalize_selection(self) -> None:
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16_384:
                    raise ValueError("invalid body size")
                data = json.loads(self.rfile.read(size))
                action = str(data["action"])
                profile = _active_profile(database)
                feedback = _read_feedback(database, profile.id)
                active = {str(result.path) for result in state.results}
                scoped = {path: item for path, item in feedback.items() if path in active}
                selected = [
                    path for path, item in scoped.items()
                    if item.get("choice") in {"keep", "edit"}
                ]
                if not selected:
                    raise ValueError("还没有保留照片")
                export_root = state.output / "exports"
                stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
                manifest = export_selection(
                    scoped, export_root / f"selection-{profile.id}-{stamp}.json", "json"
                )
                if action == "copy":
                    destination = Path(str(data["destination"])).expanduser().resolve()
                    if not destination.is_dir():
                        raise ValueError("导出文件夹不存在")
                    actions = plan_file_actions(selected, destination, "copy")
                    audit = apply_actions(actions, export_root / f"copy-audit-{stamp}.json")
                    conflicts = sum(item.status == "conflict" for item in actions)
                    if sys.platform == "darwin":
                        subprocess.Popen(["open", str(destination)])
                elif action == "xmp":
                    actions = plan_xmp_actions(scoped)
                    audit = apply_actions(actions, export_root / f"xmp-audit-{stamp}.json")
                    conflicts = sum(item.status == "conflict" for item in actions)
                else:
                    raise ValueError("unsupported finalization action")
                self._json({
                    "ok": True,
                    "action": action,
                    "created": len(audit["actions"]),
                    "conflicts": conflicts,
                    "manifest": str(manifest),
                    "audit": str(export_root / f"{action}-audit-{stamp}.json"),
                })
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

        def _load_more(self) -> None:
            if state.folder is None:
                self._json({"ok": False, "error": "请先选择照片文件夹"}, HTTPStatus.BAD_REQUEST)
                return
            new_results = state.analyzer(
                state.folder, state.output, state.limit, len(state.results)
            )
            known = {str(result.path) for result in state.results}
            additions = [result for result in new_results if str(result.path) not in known]
            state.results.extend(additions)
            self._json({"ok": True, "added": len(additions), "count": len(state.results)})

        def _build_recommendations(self) -> None:
            try:
                size = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(min(size, 16_384))) if size else {}
                requested = Path(str(data.get("folder") or state.folder or "")).expanduser().resolve()
                if not requested.is_dir():
                    self._json({"ok": False, "error": "文件夹不存在"}, HTTPStatus.BAD_REQUEST)
                    return
                with state.job_lock:
                    if state.progress_running:
                        self._json(
                            {"ok": False, "error": "已有分析任务正在运行"},
                            HTTPStatus.CONFLICT,
                        )
                        return
                    state.folder = requested
                    state.progress_running = True
                    state.progress_stage = "scanning"
                    state.progress_current = 0
                    state.progress_total = 0
                    state.progress_error = ""
                    state.cancel_event.clear()
                Thread(target=self._run_analysis_job, daemon=True).start()
                self._json({"ok": True, "accepted": True}, HTTPStatus.ACCEPTED)
            except (ValueError, json.JSONDecodeError):
                self._json({"ok": False, "error": "无效请求"}, HTTPStatus.BAD_REQUEST)

        def _run_analysis_job(self) -> None:
            def update_progress(current: int, total: int) -> None:
                state.progress_current = current
                state.progress_total = total

            def update_stats(values: dict[str, int]) -> None:
                state.cache_hits = values["hits"]
                state.cache_misses = values["misses"]
                state.cache_failed = values["failed"]
                state.cache_deleted = values["deleted"]

            try:
                all_results = state.analyzer(
                    state.folder,
                    state.output,
                    None,
                    0,
                    update_progress,
                    update_stats,
                    state.cancel_event.is_set,
                )
                if state.cancel_event.is_set():
                    state.progress_stage = "cancelled"
                    return
                state.progress_stage = "ranking"
                profile = _active_profile(database)
                feedback = _read_feedback(database, profile.id)
                scores, _, _, _, _ = _ranking(
                    all_results, database, profile, state.output / "catalog.sqlite3"
                )
                reviewed_paths = set(feedback)
                state.results = sorted(
                    all_results,
                    key=lambda result: (
                        str(result.path) in reviewed_paths,
                        -scores[str(result.path)],
                    ),
                )
                state.round_number = 1
                state.candidate_paths = []
                _refresh_candidates(state, database)
                state.progress_stage = "done"
            except Exception as exc:  # noqa: BLE001
                state.progress_error = str(exc)
                state.progress_stage = "failed"
            finally:
                state.progress_running = False

        def _rotate_photo(self) -> None:
            try:
                size = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(min(size, 16_384)))
                path = str(data["path"])
                if path not in {str(result.path) for result in state.results}:
                    raise ValueError("unknown photo")
                with _connect(database) as connection:
                    row = connection.execute(
                        "SELECT degrees FROM rotations WHERE path = ?", (path,)
                    ).fetchone()
                    degrees = ((row["degrees"] if row else 0) + 90) % 360
                    connection.execute(
                        "INSERT INTO rotations(path, degrees) VALUES (?, ?) "
                        "ON CONFLICT(path) DO UPDATE SET degrees=excluded.degrees",
                        (path, degrees),
                    )
                self._json({
                    "ok": True,
                    "degrees": degrees,
                    "candidates": _candidate_payload(state, database),
                })
            except (ValueError, KeyError, json.JSONDecodeError):
                self._json({"ok": False, "error": "invalid rotation"}, HTTPStatus.BAD_REQUEST)

        def _change_profile(self) -> None:
            try:
                size = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(min(size, 16_384)))
                profile_id = str(data["profile_id"])
                if profile_id not in {profile.id for profile in _profiles(database)}:
                    raise ValueError("unknown profile")
                with _connect(database) as connection:
                    connection.execute(
                        "UPDATE app_settings SET value = ? WHERE key = 'active_profile'",
                        (profile_id,),
                    )
                state.candidate_paths = []
                _refresh_candidates(state, database)
                self._json({
                    "ok": True,
                    "active_profile": profile_id,
                    "candidates": _candidate_payload(state, database),
                })
            except (ValueError, KeyError, json.JSONDecodeError):
                self._json({"ok": False, "error": "invalid profile"}, HTTPStatus.BAD_REQUEST)

        def _update_custom_profile(self) -> None:
            try:
                size = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(min(size, 65_536)))
                weights = {str(key): float(value) for key, value in data["weights"].items()}
                registry = default_registry(set())
                valid = set(METRIC_IDS) | {
                    criterion_id
                    for plugin in registry.describe()
                    for criterion_id in plugin["criteria"]
                }
                if not weights or not set(weights) <= valid or any(
                    value < 0 or value > 1 for value in weights.values()
                ):
                    raise ValueError("invalid weights")
                with _connect(database) as connection:
                    connection.execute(
                        """UPDATE profiles SET name = ?, weights = ?, hard_rules = ?,
                        enabled_plugins = ?, is_builtin = 0 WHERE id = 'custom'""",
                        (
                            str(data.get("name", "Custom"))[:40],
                            json.dumps(weights),
                            json.dumps(data.get("hard_rules", {})),
                            json.dumps(data.get("enabled_plugins", ["builtin.objective"])),
                        ),
                    )
                if _active_profile(database).id == "custom":
                    state.candidate_paths = []
                    _refresh_candidates(state, database)
                self._json({"ok": True})
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                self._json({"ok": False, "error": "invalid custom profile"}, HTTPStatus.BAD_REQUEST)

        def _correct_groups(self) -> None:
            try:
                size = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(min(size, 65_536)))
                source_ids = [str(value) for value in data["source_group_ids"]]
                partitions = [
                    [str(photo_id) for photo_id in partition]
                    for partition in data["partitions"]
                ]
                if not source_ids or not partitions:
                    raise ValueError("empty correction")
                with Catalog(state.output / "catalog.sqlite3") as catalog:
                    known = {str(record["id"]) for record in catalog.photo_records()}
                    requested = {photo_id for partition in partitions for photo_id in partition}
                    if not requested <= known:
                        raise ValueError("unknown photo")
                    created = catalog.save_manual_partitions(
                        source_ids, partitions, str(data.get("type", "related"))
                    )
                self._json({"ok": True, "created_group_ids": created})
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                self._json({"ok": False, "error": "invalid correction"}, HTTPStatus.BAD_REQUEST)

        def _save_group_feedback(self) -> None:
            try:
                size = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(min(size, 16_384)))
                group_id, photo_id = str(data["group_id"]), str(data["photo_id"])
                decision = str(data["decision"])
                if decision not in {"winner", "reject"}:
                    raise ValueError("invalid decision")
                with Catalog(state.output / "catalog.sqlite3") as catalog:
                    group = next(item for item in catalog.groups() if item["id"] == group_id)
                    if photo_id not in {member["id"] for member in group["members"]}:
                        raise ValueError("photo outside group")
                reasons = data.get("reason_criteria", ["group_comparison"])
                with _connect(database) as connection:
                    if decision == "winner":
                        connection.execute(
                            "DELETE FROM group_feedback WHERE group_id = ? AND decision = 'winner'",
                            (group_id,),
                        )
                    connection.execute(
                        """INSERT INTO group_feedback
                        (group_id, photo_id, decision, reason_criteria, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(group_id, photo_id) DO UPDATE SET
                        decision=excluded.decision, reason_criteria=excluded.reason_criteria,
                        updated_at=excluded.updated_at""",
                        (
                            group_id,
                            photo_id,
                            decision,
                            json.dumps(reasons),
                            datetime.now(UTC).isoformat(),
                        ),
                    )
                self._json({"ok": True})
            except (ValueError, KeyError, StopIteration, json.JSONDecodeError):
                self._json({"ok": False, "error": "invalid group feedback"}, HTTPStatus.BAD_REQUEST)

        def _change_plugin(self) -> None:
            try:
                size = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(min(size, 16_384)))
                plugin_id, enabled = str(data["plugin_id"]), data["enabled"]
                if not isinstance(enabled, bool) or plugin_id == "builtin.objective":
                    raise ValueError("invalid setting")
                registry = default_registry(set())
                known = {item["id"]: item for item in registry.describe()}
                if plugin_id not in known:
                    raise ValueError("unknown plugin")
                if enabled and known[plugin_id]["availability"] != "ready":
                    self._json(
                        {
                            "ok": False,
                            "error": known[plugin_id]["unavailable_reason"],
                            "install_hint": known[plugin_id]["install_hint"],
                        },
                        HTTPStatus.CONFLICT,
                    )
                    return
                with Catalog(state.output / "catalog.sqlite3") as catalog:
                    current = catalog.plugin_settings()
                    if not current:
                        for default_id in (
                            "builtin.objective",
                            "builtin.saliency",
                            "builtin.timing-depth",
                        ):
                            catalog.set_plugin_enabled(default_id, True)
                    catalog.set_plugin_enabled(plugin_id, enabled)
                self._json({"ok": True, "requires_analysis": enabled})
            except (ValueError, KeyError, json.JSONDecodeError):
                self._json({"ok": False, "error": "invalid plugin setting"}, HTTPStatus.BAD_REQUEST)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def serve(
    results: list[Result],
    output: Path,
    port: int,
    folder: Path | None,
    limit: int,
    analyzer: object,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    database = output / "feedback.sqlite3"
    _connect(database).close()
    state = SessionState(results, output, folder.resolve() if folder else None, limit, analyzer)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state, database))
    print(f"\n本地选片工作台：http://127.0.0.1:{port}")
    print(f"反馈数据库：{database.resolve()}")
    print("按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        server.server_close()


LEGACY_APP_HTML = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width"><title>RAW Photo Curator</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#0d0e10;color:#eee;font:15px system-ui}
header{min-height:58px;display:flex;align-items:center;gap:12px;padding:9px 24px;border-bottom:1px solid #292b30}
header b{font-size:17px}#summary{color:#a6a8ad}.layout{height:calc(100vh - 58px);display:grid;grid-template-columns:minmax(0,1fr) 340px}
#folder{min-width:280px;flex:1;background:#17191d;color:#eee;border:1px solid #3b3e44;border-radius:8px;padding:8px}#folderStatus{font-size:12px;color:#8e929a}
#scanProgress{width:150px;accent-color:#55c987}#scanProgress[hidden]{display:none}
.stage{display:flex;align-items:center;justify-content:center;padding:24px;background:#08090a;overflow:hidden}.stage img{max-width:100%;max-height:100%;object-fit:contain}
aside{padding:20px;overflow:auto;border-left:1px solid #292b30}h1{font-size:18px;margin:0 0 6px;overflow-wrap:anywhere}.path{font-size:12px;color:#777;overflow-wrap:anywhere}
.scores{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:18px 0}.score{padding:13px;border-radius:10px;background:#1b1d21}.score strong{display:block;font-size:26px}.keep strong{color:#64dc90}.edit strong{color:#6dbaff}
.metrics{display:grid;grid-template-columns:1fr 1fr;gap:8px}.metric{background:#15171a;padding:9px;border-radius:8px}.metric span{display:block;color:#888;font-size:12px}.metric b{font-size:18px}
.choices,.tags{display:flex;gap:7px;flex-wrap:wrap;margin:16px 0}button{border:1px solid #444;background:#202228;color:#eee;border-radius:8px;padding:9px 11px;cursor:pointer}button:hover{background:#30333a}button.active{background:#17613a;border-color:#45ce7b}.reject.active{background:#792727;border-color:#ee6868}.edit.active{background:#185987;border-color:#61baff}
.tags button{font-size:12px;padding:6px 8px}textarea{width:100%;background:#15171a;color:#eee;border:1px solid #3b3e44;border-radius:8px;padding:9px;resize:vertical}.nav{display:flex;justify-content:space-between;margin-top:16px}.hint{color:#73767d;font-size:12px;line-height:1.6;margin-top:16px}
@media(max-width:800px){.layout{height:auto;grid-template-columns:1fr}.stage{height:55vh}aside{border-left:0}.path{display:none}}
</style>
<header><b>RAW Photo Curator</b><input id="folder" aria-label="本地照片文件夹"><button id="loadFolder">加载前 5 张</button><button id="recommend">生成推荐榜</button><progress id="scanProgress" value="0" max="100" hidden></progress><span id="folderStatus"></span><span id="position"></span><span id="summary">载入中…</span></header>
<div class="layout"><div class="stage"><img id="photo"></div><aside><h1 id="name"></h1><div class="path" id="path"></div>
<div class="scores"><div class="score keep">客观保留分<strong id="keep"></strong></div><div class="score edit">个人推荐分<strong id="recommendScore"></strong></div><div class="score edit">调色潜力<strong id="edit"></strong></div></div>
<div class="metrics" id="metrics"></div><div class="choices"><button data-choice="keep">P 保留</button><button class="edit" data-choice="edit">E 调色</button><button data-choice="maybe">M 待定</button><button class="reject" data-choice="reject">X 淘汰</button></div>
<div>原因标签</div><div class="tags" id="tags"></div>
<textarea id="note" rows="3" maxlength="300" placeholder="备注会自动保存"></textarea><div class="nav"><button id="prev">← 上一张</button><button id="next">下一张 →</button></div>
<div class="hint">快捷键：P 保留 · E 调色 · M 待定 · X 淘汰 · ←/→ 切换<br>客观分来自预览像素的清晰度、曝光、高光、阴影、色彩与画面区域信息。</div></aside></div>
<script>
let photos=[], index=0, timer; const labels={sharpness:'清晰度',exposure:'曝光',highlights:'高光保留',shadows:'阴影保留',color:'色彩信息',composition:'构图代理'};
const photoEl=document.getElementById('photo'), nameEl=document.getElementById('name'), pathEl=document.getElementById('path');
const keepEl=document.getElementById('keep'), editEl=document.getElementById('edit'), positionEl=document.getElementById('position');
const recommendScoreEl=document.getElementById('recommendScore');
const metricsEl=document.getElementById('metrics'), tagsEl=document.getElementById('tags');
const noteEl=document.getElementById('note'), summaryEl=document.getElementById('summary');
const tagNames=['构图','光线','清晰度','表情','色彩','景深'];
async function load(reset=true){const r=await fetch('/api/photos');const d=await r.json();photos=d.photos;if(reset)index=0;document.getElementById('folder').value=d.folder;showSummary(d.summary);render()}
function current(){return photos[index]} function fb(){return current().feedback ||= {choice:null,tags:[],note:''}}
function render(){const p=current();if(!p)return;photoEl.src=p.thumbnail;nameEl.textContent=p.path.split('/').pop();pathEl.textContent=p.path;keepEl.textContent=p.keep_score;recommendScoreEl.textContent=p.recommendation_score;editEl.textContent=p.edit_score;positionEl.textContent=`${index+1} / ${photos.length}`;
 metricsEl.innerHTML=Object.entries(p.metrics).map(([k,v])=>`<div class="metric"><span>${labels[k]}</span><b>${v}</b></div>`).join('');
 document.querySelectorAll('[data-choice]').forEach(b=>b.classList.toggle('active',b.dataset.choice===fb().choice));
 tagsEl.innerHTML=tagNames.map(t=>`<button data-tag="${t}" class="${fb().tags.includes(t)?'active':''}">${t}</button>`).join('');noteEl.value=fb().note||''}
async function save(){const p=current(), f=fb();const r=await fetch('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:p.id,...f})});const d=await r.json();if(d.ok){showSummary(d.summary);document.getElementById('folderStatus').textContent='偏好模型已更新'}}
function showSummary(s){summaryEl.textContent=`已评 ${s.reviewed} · 保留 ${s.keep} · 调色 ${s.edit} · 待定 ${s.maybe} · 淘汰 ${s.reject}`}
function choose(c){fb().choice=fb().choice===c?null:c;render();save()} function move(n){index=Math.max(0,Math.min(photos.length-1,index+n));render()}
async function moveNext(){if(index<photos.length-1){move(1);return}const status=document.getElementById('folderStatus');status.textContent='正在加载下一批…';const r=await fetch('/api/more',{method:'POST'});const d=await r.json();if(d.added){const oldLength=photos.length;await load(false);index=oldLength;render();status.textContent=`已加载 ${d.count} 张`}else{status.textContent='已经是最后一张'}}
document.querySelector('.choices').onclick=e=>{if(e.target.dataset.choice)choose(e.target.dataset.choice)};
tagsEl.onclick=e=>{const t=e.target.dataset.tag;if(t){fb().tags=fb().tags.includes(t)?fb().tags.filter(x=>x!==t):[...fb().tags,t];render();save()}};
noteEl.oninput=()=>{fb().note=noteEl.value;clearTimeout(timer);timer=setTimeout(save,350)};
document.getElementById('prev').onclick=()=>move(-1);
document.getElementById('next').onclick=moveNext;
document.getElementById('loadFolder').onclick=async()=>{const status=document.getElementById('folderStatus');status.textContent='正在分析…';const r=await fetch('/api/folder',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folder:document.getElementById('folder').value})});const d=await r.json();status.textContent=d.ok?`已加载 ${d.count} 张`:d.error;if(d.ok)await load()};
document.getElementById('recommend').onclick=async()=>{const status=document.getElementById('folderStatus'),bar=document.getElementById('scanProgress'),button=document.getElementById('recommend');status.textContent='正在分析全部照片…';bar.hidden=false;bar.value=0;button.disabled=true;const poll=setInterval(async()=>{const p=await (await fetch('/api/progress')).json();if(p.total){bar.value=p.current/p.total*100;status.textContent=p.stage==='ranking'?'正在更新个人推荐排序…':`正在分析 ${p.current} / ${p.total}（${Math.round(p.current/p.total*100)}%）`}},500);try{const r=await fetch('/api/recommendations',{method:'POST'});const d=await r.json();if(d.ok){await load();status.textContent=`推荐榜已生成：${d.count} 张，基于 ${d.feedback_count} 条反馈`}else status.textContent='推荐生成失败'}finally{clearInterval(poll);bar.value=100;setTimeout(()=>bar.hidden=true,1200);button.disabled=false}};
document.onkeydown=e=>{if(e.target===noteEl||e.target.id==='folder')return;if(e.key==='ArrowRight')moveNext();if(e.key==='ArrowLeft')move(-1);if('pPeEmMxX'.includes(e.key))choose(({p:'keep',e:'edit',m:'maybe',x:'reject'})[e.key.toLowerCase()])};load();
</script></html>"""


APP_HTML = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width"><title>RAW Photo Curator</title>
<style>
:root{color-scheme:dark;--bg:#090b10;--surface:#11141b;--surface2:#181c24;--line:#262b36;--text:#f4f5f7;--muted:#9198a6;--accent:#7cf3ad;--danger:#ff8585}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% -10%,#1c2730 0,transparent 32%),var(--bg);color:var(--text);font:15px Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}button,input{font:inherit}
header{position:sticky;top:0;z-index:5;padding:18px 4vw 16px;border-bottom:1px solid #ffffff0f;background:#090b10de;backdrop-filter:blur(20px)}.topline{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}.brand{display:flex;align-items:center;gap:11px}.mark{display:grid;place-items:center;width:34px;height:34px;border-radius:11px;background:linear-gradient(145deg,#83f7b4,#58bfff);color:#07100b;font-weight:900}.brand h1{font-size:17px;letter-spacing:-.02em;margin:0}.brand small{display:block;color:var(--muted);font-size:11px;margin-top:2px}.meta{display:flex;gap:8px}.chip{padding:6px 10px;border:1px solid var(--line);border-radius:999px;color:#b9bec8;background:#ffffff05;font-size:12px}
.controls{display:grid;grid-template-columns:minmax(260px,1fr) auto auto auto;gap:10px}.pathbox{display:flex;align-items:center;gap:9px;padding:0 13px;border:1px solid var(--line);border-radius:12px;background:#0d1016;transition:.2s}.pathbox:focus-within{border-color:#65d998;box-shadow:0 0 0 3px #65d99814}.pathbox span{color:#697181}input{width:100%;border:0;outline:0;background:transparent;color:var(--text);padding:12px 0}select,button{border:1px solid var(--line);background:#20252f;color:var(--text);border-radius:11px;padding:10px 14px;cursor:pointer;transition:.18s}button:hover{border-color:#4c5565;transform:translateY(-1px)}button:disabled{opacity:.5;cursor:default;transform:none}#chooseFolder{background:#1c372b;border-color:#3a7655;color:#caffe0;font-weight:700}#start{background:var(--text);color:#0b0d11;border:0;font-weight:750;padding-inline:20px}.activity{display:flex;align-items:center;gap:12px;min-height:20px;margin-top:10px}progress{width:180px;height:5px;border:0;accent-color:var(--accent)}#status{color:var(--muted);font-size:12px}
main{max-width:1540px;margin:auto;padding:28px 4vw 60px}.grid{display:grid;grid-template-columns:1fr;gap:26px}.card{position:relative;display:grid;grid-template-columns:minmax(0,1.8fr) minmax(350px,.82fr);background:linear-gradient(145deg,#171b23,#11141a);border:1px solid var(--line);border-radius:20px;overflow:hidden;box-shadow:0 18px 60px #0005}.card.kept{border-color:#56d88d;box-shadow:0 18px 60px #0005,0 0 0 1px #56d88d55}.rank{position:absolute;z-index:2;top:14px;left:14px;padding:7px 10px;border:1px solid #ffffff26;border-radius:10px;background:#07090ba8;backdrop-filter:blur(12px);font-weight:800}.photo-wrap{position:relative;display:grid;place-items:center;min-height:470px;background:linear-gradient(145deg,#08090c,#0e1116)}.card img{display:block;width:100%;height:100%;max-height:72vh;object-fit:contain}.body{display:flex;flex-direction:column;padding:23px}.eyebrow{color:var(--accent);font-size:11px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}.title-row{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:5px}.name{font-size:18px;font-weight:750;overflow-wrap:anywhere}.rotate{position:absolute;z-index:2;top:14px;right:14px;padding:9px 13px;white-space:nowrap;background:#07090bc7;border-color:#ffffff2b;backdrop-filter:blur(12px);box-shadow:0 5px 18px #0006}.rotate:hover{background:#20252ee8}.score{display:flex;gap:9px;margin:16px 0 12px}.score span{display:flex;flex-direction:column;gap:2px;flex:1;padding:10px 12px;border:1px solid var(--line);border-radius:12px;color:var(--muted);font-size:11px;background:#0b0e13}.score strong{font-size:22px;color:var(--text);line-height:1.1}.reason{min-height:44px;color:#c8ccd4;line-height:1.55}.evidence{display:flex;gap:5px;flex-wrap:wrap}.evidence span{padding:4px 7px;border-radius:7px;background:#ffffff08;color:#aeb5c0;font-size:10px}.evidence .unknown{color:#ffcf87}.radar{display:block;width:100%;max-width:280px;margin:auto}.radar .gridline{fill:none;stroke:#343a46;stroke-width:1}.radar .axis{stroke:#2b303a}.radar .shape{fill:#72eca535;stroke:#72eca5;stroke-width:2}.radar text{fill:#aeb4bf;font:11px system-ui}.actions{display:grid;grid-template-columns:1.25fr 1fr;gap:9px;margin-top:auto;padding-top:14px}.actions button{font-weight:700}.keep{background:#22633e;border-color:#388d5b}.keep:hover{background:#2b784b}.reject{background:#302328;border-color:#5a343d;color:#ffc4c4}.reject:hover{background:#48282f}.badge{color:var(--accent);font-size:12px;margin-top:9px}
.welcome{display:grid;min-height:calc(100vh - 190px);place-items:center}.welcome-card{width:min(760px,94vw);padding:46px;border:1px solid #2c3833;border-radius:28px;background:radial-gradient(circle at 75% 10%,#233a3266,transparent 38%),linear-gradient(145deg,#171c22,#0e1116);box-shadow:0 30px 90px #0007;text-align:center}.welcome-mark{display:grid;place-items:center;width:72px;height:72px;margin:0 auto 22px;border-radius:22px;background:linear-gradient(145deg,#83f7b4,#58bfff);color:#07100b;font-size:32px;font-weight:900}.welcome h2{margin:0;font-size:34px;letter-spacing:-.04em}.welcome p{max-width:580px;margin:15px auto 25px;color:#aeb5c0;line-height:1.7}.welcome button{padding:14px 22px;background:#dfffea;color:#0b1710;border:0;font-weight:800}.welcome-points{display:flex;justify-content:center;gap:18px;flex-wrap:wrap;margin-top:24px;color:#78818f;font-size:12px}.welcome-points span{padding:7px 10px;border:1px solid var(--line);border-radius:999px;background:#090c10}
.finish-card{max-height:92vh;overflow:auto}.finish-overview{margin:-8px 0 20px}.finish-strip{display:grid;grid-template-columns:repeat(6,1fr);gap:6px;margin:0 0 14px}.finish-thumb{position:relative;overflow:hidden;border-radius:9px;background:#080a0d;aspect-ratio:1}.finish-thumb img{width:100%;height:100%;object-fit:cover}.finish-thumb span{position:absolute;inset:auto 4px 4px;padding:3px 5px;border-radius:5px;background:#050608cc;font-size:9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.finish-insight{display:grid;grid-template-columns:1fr 1fr;gap:9px}.finish-insight div{padding:12px;border:1px solid var(--line);border-radius:12px;background:#090c10;color:#aab1bd;font-size:12px;line-height:1.55}.finish-insight strong{display:block;margin-bottom:3px;color:var(--text)}
@media(max-width:900px){header{position:relative}.card{grid-template-columns:1fr}.photo-wrap{min-height:0}.card img{max-height:none;aspect-ratio:3/2}.radar{max-width:250px}.finish-strip{grid-template-columns:repeat(4,1fr)}}@media(max-width:640px){.controls{grid-template-columns:1fr}.topline{align-items:flex-start}.meta{flex-direction:column}.card{border-radius:15px}.body{padding:18px}.welcome-card{padding:30px 20px}.welcome h2{font-size:27px}.finish-insight{grid-template-columns:1fr}}
.group-panel{position:fixed;inset:0;z-index:20;display:none;background:#07090bf2;backdrop-filter:blur(16px);overflow:auto;padding:24px 4vw 60px}.group-panel.open{display:block}.group-toolbar{position:sticky;top:0;z-index:3;display:flex;align-items:center;gap:12px;padding:12px 0 18px;background:#07090bf2}.group-toolbar h2{margin:0}.group-progress{margin-right:auto;color:var(--muted);font-size:12px}.group-list{max-width:1400px;margin:auto}.similarity-group{padding:20px;border:1px solid var(--line);border-radius:18px;background:var(--surface)}.group-head{display:flex;gap:10px;align-items:center;margin-bottom:16px}.group-head strong{margin-right:auto}.member-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}.member-card{position:relative}.member-choice{position:relative;display:block;width:100%;overflow:hidden;padding:0;border:1px solid var(--line);border-radius:14px;background:#050608;aspect-ratio:3/2}.member-choice:hover{border-color:var(--accent);transform:translateY(-2px)}.member-choice img{width:100%;height:100%;object-fit:contain}.member-choice span{position:absolute;left:10px;right:10px;bottom:10px;padding:8px 10px;border-radius:9px;background:#050608dc;color:var(--text);text-align:left}.group-rotate{position:absolute;z-index:2;top:9px;right:9px;padding:7px 10px;background:#050608d9;border-color:#ffffff35;box-shadow:0 4px 15px #0007}.group-done{display:grid;place-items:center;min-height:50vh;color:var(--muted);font-size:18px}
.settings-panel{position:fixed;inset:0;z-index:30;display:none;place-items:center;background:#05070acc;backdrop-filter:blur(14px);padding:20px}.settings-panel.open{display:grid}.settings-card{width:min(760px,96vw);max-height:88vh;overflow:auto;padding:22px;border:1px solid var(--line);border-radius:20px;background:#12161d}.settings-head{display:flex;align-items:center}.settings-head h2{margin:0 auto 5px 0}.plugin-list{display:grid;gap:10px;margin-top:15px}.plugin-card{display:grid;grid-template-columns:auto 1fr auto;gap:12px;align-items:start;padding:14px;border:1px solid var(--line);border-radius:13px;background:#0b0e13}.plugin-card input{width:auto}.plugin-card strong{display:block}.plugin-card p{margin:5px 0;color:var(--muted);font-size:12px;line-height:1.45}.plugin-meta{display:flex;gap:6px;flex-wrap:wrap}.plugin-meta span{padding:3px 6px;border-radius:6px;background:#ffffff08;color:#abb1bc;font-size:10px}.unavailable{color:#ffba82;font-size:11px}
.custom-editor,.model-editor{margin-top:18px;padding-top:16px;border-top:1px solid var(--line)}.custom-editor h3,.model-editor h3{margin:0 0 10px}.weight-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:7px}.weight-row{display:grid;grid-template-columns:1fr 80px;gap:8px;align-items:center;color:#bcc2cc;font-size:12px}.weight-row input{padding:7px 9px;border:1px solid var(--line);border-radius:8px;background:#090c11}.custom-editor button,.model-editor button{margin-top:12px}.model-status{color:var(--muted);line-height:1.6;font-size:12px}.model-actions{display:flex;gap:8px;flex-wrap:wrap}.model-actions input{display:none}
.finish-panel{position:fixed;inset:0;z-index:40;display:none;place-items:center;background:#05070add;backdrop-filter:blur(18px);padding:20px}.finish-panel.open{display:grid}.finish-card{width:min(720px,96vw);padding:26px;border:1px solid #344039;border-radius:22px;background:linear-gradient(145deg,#171d1a,#10141a);box-shadow:0 28px 90px #000a}.finish-head{display:flex;align-items:start;gap:16px}.finish-head div{margin-right:auto}.finish-head h2{margin:0 0 6px;font-size:25px}.finish-head p{margin:0;color:var(--muted);line-height:1.6}.finish-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:22px 0}.finish-stat{padding:15px;border:1px solid var(--line);border-radius:14px;background:#090c10;color:var(--muted);font-size:12px}.finish-stat strong{display:block;margin-top:4px;color:var(--text);font-size:26px}.finish-actions{display:grid;grid-template-columns:1fr 1fr;gap:10px}.finish-actions button{padding:14px;font-weight:750}.finish-actions .primary{background:#27734a;border-color:#409a67}.finish-note{margin:15px 0 0;color:var(--muted);font-size:12px;line-height:1.55}.finish-result{min-height:22px;margin-top:14px;color:#bfffd5;font-size:13px;overflow-wrap:anywhere}
</style><header><div class="topline"><div class="brand"><div class="mark">R</div><div><h1>RAW Photo Curator</h1><small>Local-first photo selection</small></div></div><div class="meta"><span class="chip" id="round">第 1 轮</span><span class="chip" id="summary">尚未选择</span></div></div><div class="controls"><label class="pathbox"><span>⌁</span><input id="folder" aria-label="本地照片文件夹" placeholder="选择包含 ARW / JPG 的照片文件夹"></label><button id="chooseFolder">在 Finder 中选择…</button><select id="profile" aria-label="选片标准"></select><div><button id="settingsButton">标准设置</button> <button id="groupsButton">连拍选最佳</button> <button id="finishButton">完成选片</button> <button id="cancel" hidden>取消</button> <button id="start">重新分析</button></div></div><div class="activity"><progress id="progress" value="0" max="100" hidden></progress><span id="status">点击“在 Finder 中选择”，照片不会离开这台设备</span></div></header>
<main><div class="grid" id="grid"></div></main><section class="group-panel" id="groupPanel"><div class="group-toolbar"><h2>选出这一组最好的照片</h2><span class="group-progress" id="groupProgress"></span><button id="closeGroups">关闭</button></div><div class="group-list" id="groupList"></div></section><section class="settings-panel" id="settingsPanel"><div class="settings-card"><div class="settings-head"><div><h2>分析标准</h2><span id="settingsHint">所有分析默认在本机完成</span></div><button id="closeSettings">关闭</button></div><div class="plugin-list" id="pluginList"></div><div class="custom-editor" id="customEditor"></div><div class="model-editor" id="modelEditor"></div></div></section><section class="finish-panel" id="finishPanel"><div class="finish-card"><div class="finish-head"><div><h2>选片完成</h2><p>评价已自动保存。现在可以把保留的原始照片交给下一步工作流。</p></div><button id="closeFinish">关闭</button></div><div class="finish-stats"><div class="finish-stat">保留<strong id="finishKept">0</strong></div><div class="finish-stat">淘汰<strong id="finishRejected">0</strong></div><div class="finish-stat">尚未评价<strong id="finishUnreviewed">0</strong></div></div><div class="finish-actions"><button class="primary" id="copySelection">复制保留照片到文件夹…</button><button id="xmpSelection">生成 XMP 标记</button></div><p class="finish-note">复制会保留原始 RAW 不变；XMP 会在原片旁创建兼容 Lightroom / Capture One 的 sidecar。两种操作都会生成可撤销的审计记录。</p><div class="finish-result" id="finishResult"></div></div></section>
<script>
const folder=document.getElementById('folder'),profile=document.getElementById('profile'),start=document.getElementById('start'),cancel=document.getElementById('cancel'),bar=document.getElementById('progress'),statusEl=document.getElementById('status'),grid=document.getElementById('grid'),roundEl=document.getElementById('round'),summaryEl=document.getElementById('summary'),chooseFolder=document.getElementById('chooseFolder');
const groupPanel=document.getElementById('groupPanel'),groupList=document.getElementById('groupList'),groupProgress=document.getElementById('groupProgress');let groupsData=[],groupIndex=0;
const settingsPanel=document.getElementById('settingsPanel'),pluginList=document.getElementById('pluginList');
const escapeHTML=value=>String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
function renderWelcome(){grid.innerHTML='<section class="welcome"><div class="welcome-card"><div class="welcome-mark">R</div><h2>从一整个文件夹，找到值得留下的照片</h2><p>选择 ARW、JPEG、PNG 或 TIFF 文件夹。程序会在本机分析画质与构图，并随着你的保留和淘汰逐步学习偏好。</p><button id="welcomeChoose">选择照片文件夹</button><div class="welcome-points"><span>完全本地处理</span><span>不修改原始 RAW</span><span>结果可导出到 Lightroom</span></div></div></section>';document.getElementById('welcomeChoose').onclick=()=>chooseFolder.click()}
async function loadCandidates(){const d=await(await fetch('/api/candidates')).json();folder.value=d.folder;start.disabled=!d.folder;roundEl.textContent=`第 ${d.round} 轮`;summaryEl.textContent=d.candidates.length?`累计保留 ${d.summary.keep} · 淘汰 ${d.summary.reject}`:'准备开始';d.candidates.length?render(d.candidates):renderWelcome()}
async function loadProfiles(){const d=await(await fetch('/api/profiles')).json();profile.innerHTML=d.profiles.map(p=>`<option value="${p.id}">${escapeHTML(p.name)}</option>`).join('');profile.value=d.active_profile}
const axes=[['清晰',m=>m.sharpness],['曝光',m=>m.exposure],['动态范围',m=>(m.highlights+m.shadows)/2],['对比',m=>m.contrast],['色彩',m=>(m.color+m.white_balance)/2],['构图',m=>m.composition]];
function polygon(values,radius){return values.map((value,i)=>{const angle=-Math.PI/2+i*Math.PI/3,r=radius*value/100;return `${110+Math.cos(angle)*r},${100+Math.sin(angle)*r}`}).join(' ')}
function radar(metrics){const values=axes.map(axis=>Math.round(axis[1](metrics)));const grids=[25,50,75,100].map(level=>`<polygon class="gridline" points="${polygon(Array(6).fill(level),72)}"/>`).join('');const lines=axes.map((_,i)=>{const a=-Math.PI/2+i*Math.PI/3;return `<line class="axis" x1="110" y1="100" x2="${110+Math.cos(a)*72}" y2="${100+Math.sin(a)*72}"/>`}).join('');const labels=axes.map((axis,i)=>{const a=-Math.PI/2+i*Math.PI/3,x=110+Math.cos(a)*91,y=104+Math.sin(a)*86;return `<text x="${x}" y="${y}" text-anchor="middle">${axis[0]} ${values[i]}</text>`}).join('');return `<svg class="radar" viewBox="0 0 220 200" role="img" aria-label="六项照片评分"><title>六项照片评分</title>${grids}${lines}<polygon class="shape" points="${polygon(values,72)}"/>${labels}</svg>`}
const explanations={清晰:'主体细节清楚，焦点表现较可靠。',曝光:'整体曝光均衡，中间调保留自然。',动态范围:'高光和暗部保留完整，后期调整空间较大。',对比:'明暗层次清楚，画面结构比较突出。',色彩:'色彩关系协调，整体观感较自然。',构图:'视觉信息集中，画面边缘干扰较少。'};
function reason(metrics){const best=axes.map(axis=>[axis[0],axis[1](metrics)]).sort((a,b)=>b[1]-a[1]).slice(0,2);return best.map(item=>explanations[item[0]]).join(' ')}
function evidence(criteria){return `<div class="evidence">${(criteria||[]).filter(c=>c.id.startsWith('raw.')||c.id.startsWith('subject.')||c.id.startsWith('depth.')||c.id.startsWith('timing.')||c.id.endsWith('horizon')||c.id.endsWith('edge_integrity')).map(c=>`<span class="${c.score===null?'unknown':''}">${c.label} ${typeof c.value==='number'?Math.round(c.value):'未知'}${c.group_percentile?` · 组内 P${c.group_percentile}`:''}</span>`).join('')}</div>`}
function render(items){grid.innerHTML=items.map((p,i)=>{const name=escapeHTML(p.path.split('/').pop()),learned=(p.score_contributions||[]).map(x=>`${escapeHTML(x.feature)} ${x.effect>0?'+':''}${x.effect}`).join(' · ');return `<article class="card ${p.kept?'kept':''}"><div class="rank">#${i+1}</div><div class="photo-wrap"><img src="${p.thumbnail}" alt="${name}"><button class="rotate" title="顺时针旋转 90°" aria-label="旋转 ${name}" data-path="${encodeURIComponent(p.path)}" data-rotate="true">↻ 旋转 90°</button></div><div class="body"><div class="eyebrow">Top recommendation</div><div class="title-row"><div class="name">${name}</div></div><div class="score"><span>个人推荐<strong>${p.recommendation_score}</strong></span><span>显式标准<strong>${p.profile_score}</strong></span></div><div class="reason">${reason(p.metrics)}${learned?`<br><small>学习偏好 ${(p.recommendation_confidence*100).toFixed(0)}% · ${learned}</small>`:''}</div>${evidence(p.criteria)}${radar(p.metrics)}<div class="actions"><button class="keep" data-path="${encodeURIComponent(p.path)}" data-choice="keep">✓ 保留</button><button class="reject" data-path="${encodeURIComponent(p.path)}" data-choice="reject">淘汰</button></div>${p.kept?'<div class="badge">已加入本轮候选</div>':''}</div></article>`}).join('')}
grid.onclick=async e=>{const pathValue=e.target.dataset.path;if(!pathValue)return;const path=decodeURIComponent(pathValue);if(e.target.dataset.rotate){e.target.disabled=true;const d=await(await fetch('/api/rotation',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})})).json();if(d.ok){render(d.candidates);statusEl.textContent=`预览已旋转 ${d.degrees}°`}return}const choice=e.target.dataset.choice;if(!choice)return;e.target.disabled=true;const before=roundEl.textContent;const d=await(await fetch('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path,choice})})).json();if(d.ok){roundEl.textContent=`第 ${d.round} 轮`;summaryEl.textContent=`累计保留 ${d.summary.keep} · 淘汰 ${d.summary.reject}`;render(d.candidates);statusEl.textContent=before!==roundEl.textContent?'已选满 5 张，偏好模型已更新并生成新一轮':'偏好模型已更新'}};
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function followJob(){while(true){const p=await(await fetch('/api/progress')).json();if(p.total)bar.value=p.current/p.total*100;if(p.stage==='ranking')statusEl.textContent='正在根据反馈生成 Top 5…';else if(p.running)statusEl.textContent=`正在分析 ${p.current} / ${p.total}（${p.total?Math.round(p.current/p.total*100):0}%）`;if(!p.running){if(p.stage==='done'){await loadCandidates();statusEl.textContent=`已分析 ${p.total} 张 · 缓存 ${p.cache_hits} · 新分析 ${p.cache_misses}${p.failed?` · 跳过 ${p.failed}`:''}`}else if(p.stage==='cancelled')statusEl.textContent='已取消；下次会从缓存进度继续';else statusEl.textContent=p.error||'分析失败';break}await wait(350)}}
start.onclick=async()=>{start.disabled=true;cancel.disabled=false;cancel.hidden=false;bar.hidden=false;bar.value=0;statusEl.textContent='正在建立本地照片索引…';try{const d=await(await fetch('/api/recommendations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folder:folder.value})})).json();if(d.ok)await followJob();else statusEl.textContent=d.error||'分析失败'}finally{bar.value=100;setTimeout(()=>bar.hidden=true,1000);cancel.hidden=true;start.disabled=false}};
chooseFolder.onclick=async()=>{chooseFolder.disabled=true;statusEl.textContent='正在打开 Finder…';try{const d=await(await fetch('/api/folder-picker',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({purpose:'photos'})})).json();if(d.ok){folder.value=d.folder;start.disabled=false;statusEl.textContent='已选择照片文件夹，开始本地分析…';start.click()}else if(!d.cancelled)statusEl.textContent=d.error||'无法打开文件夹选择器';else statusEl.textContent='已取消选择'}finally{chooseFolder.disabled=false}};
cancel.onclick=async()=>{cancel.disabled=true;await fetch('/api/cancel',{method:'POST'});statusEl.textContent='正在安全停止…'};
profile.onchange=async()=>{profile.disabled=true;const d=await(await fetch('/api/profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile_id:profile.value})})).json();if(d.ok){render(d.candidates);statusEl.textContent=`已切换到 ${profile.options[profile.selectedIndex].text} 标准并重新排序`}profile.disabled=false};
function renderGroup(){if(groupIndex>=groupsData.length){groupProgress.textContent=`已完成 ${groupsData.length} 组`;groupList.innerHTML='<div class="group-done">全部完成，可以关闭了</div>';return}const g=groupsData[groupIndex];groupProgress.textContent=`${groupIndex+1} / ${groupsData.length}`;groupList.innerHTML=`<article class="similarity-group"><div class="group-head"><strong>${g.type==='duplicate'?'近似重复':'连拍'} · ${g.members.length} 张</strong><span>点击你最想保留的一张</span></div><div class="member-grid">${g.members.map(m=>{const name=escapeHTML(m.path.split('/').pop());return `<div class="member-card"><button class="member-choice" data-photo="${m.id}" data-feedback-group="${g.id}" aria-label="选 ${name} 为最佳"><img src="${m.thumbnail||''}" alt="${name}"><span>${name} · 选为最佳</span></button><button class="group-rotate" data-rotate-path="${encodeURIComponent(m.path)}" aria-label="旋转 ${name}">↻ 旋转</button></div>`}).join('')}</div></article>`}
async function loadGroups(){const d=await(await fetch('/api/groups')).json();groupsData=d.groups.filter(g=>!g.members.some(m=>m.decision==='winner'));groupIndex=0;renderGroup();groupPanel.classList.add('open')}
document.getElementById('groupsButton').onclick=loadGroups;document.getElementById('closeGroups').onclick=()=>groupPanel.classList.remove('open');
groupList.onclick=async e=>{const rotate=e.target.closest('[data-rotate-path]');if(rotate){rotate.disabled=true;const path=decodeURIComponent(rotate.dataset.rotatePath),d=await(await fetch('/api/rotation',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})})).json();if(d.ok){const image=rotate.closest('.member-card').querySelector('img'),url=new URL(image.src);url.searchParams.set('r',d.degrees);image.src=url.pathname+url.search;rotate.disabled=false}return}const choice=e.target.closest('[data-photo]');if(!choice)return;choice.disabled=true;const response=await fetch('/api/group-feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({group_id:choice.dataset.feedbackGroup,photo_id:choice.dataset.photo,decision:'winner',reason_criteria:['group_comparison']})});if(response.ok){groupIndex+=1;renderGroup()}else{choice.disabled=false;statusEl.textContent='保存失败，请重试'}};
async function loadPlugins(){const [d,pd,md,ed]=await Promise.all([fetch('/api/plugins').then(r=>r.json()),fetch('/api/profiles').then(r=>r.json()),fetch('/api/model').then(r=>r.json()),fetch('/api/evaluation').then(r=>r.json())]);pluginList.innerHTML=d.plugins.map(p=>`<label class="plugin-card"><input type="checkbox" data-plugin="${p.id}" ${p.enabled?'checked':''} ${p.id==='builtin.objective'||p.availability!=='ready'?'disabled':''}><div><strong>${escapeHTML(p.name)}</strong><p>${escapeHTML(p.description)}</p><div class="plugin-meta"><span>${p.runtime_cost}</span><span>${p.download_size_mb?p.download_size_mb+' MB':'无需下载'}</span><span>${escapeHTML(p.privacy)}</span></div>${p.availability!=='ready'?`<div class="unavailable">未安装 · ${escapeHTML(p.install_hint||p.unavailable_reason)}</div>`:''}</div><span>${p.criteria.length} 项</span></label>`).join('');const custom=pd.profiles.find(p=>p.id==='custom');document.getElementById('customEditor').innerHTML=`<h3>Custom Profile 权重</h3><div class="weight-grid">${Object.entries(custom.weights).map(([key,value])=>`<label class="weight-row"><span>${escapeHTML(key)}</span><input type="number" min="0" max="1" step=".01" value="${value}" data-weight="${key}"></label>`).join('')}</div><button id="saveWeights">保存 Custom 权重</button>`;document.getElementById('saveWeights').onclick=saveCustomWeights;const model=document.getElementById('modelEditor'),evaluation=ed.test_count?`<br>本地留出 ${ed.test_count} 张 · 个人 Pairwise ${Math.round(ed.personal_ranker.pairwise_accuracy*100)}% · 显式基线 ${Math.round(ed.explicit_profile.pairwise_accuracy*100)}% · NDCG ${ed.personal_ranker.ndcg}${ed.personalization_improved?' · 已超过基线':' · 尚未超过基线'}`:'';model.innerHTML=`<h3>本地偏好模型</h3><div class="model-status">${md.ready?`${md.model.training_count} 条反馈 · 训练对一致率 ${Math.round(md.model.validation_accuracy*100)}% · 当前参与排序 ${Math.round(md.learned_weight*100)}%`:'至少需要 2 张保留和 2 张淘汰；此前使用显式标准与通用先验。'}${evaluation}</div><div class="model-actions"><button id="exportModel" ${md.ready?'':'disabled'}>导出模型</button><label><button id="importModel">导入模型</button><input id="modelFile" type="file" accept="application/json"></label><button id="resetModel">重置当前标准</button></div>`;document.getElementById('exportModel').onclick=()=>{if(!md.model)return;const link=document.createElement('a');link.href=URL.createObjectURL(new Blob([JSON.stringify(md.model,null,2)],{type:'application/json'}));link.download=`raw-curator-${md.profile_id}-model.json`;link.click()};document.getElementById('importModel').onclick=()=>document.getElementById('modelFile').click();document.getElementById('modelFile').onchange=async e=>{const data=JSON.parse(await e.target.files[0].text());const r=await(await fetch('/api/model/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})).json();statusEl.textContent=r.ok?'模型已导入并重新排序':r.error;if(r.ok){await loadCandidates();await loadPlugins()}};document.getElementById('resetModel').onclick=async()=>{const r=await(await fetch('/api/model/reset',{method:'POST'})).json();if(r.ok){statusEl.textContent='当前标准的反馈与个人模型已重置';await loadCandidates();await loadPlugins()}};settingsPanel.classList.add('open')}
async function saveCustomWeights(){const weights=Object.fromEntries([...document.querySelectorAll('[data-weight]')].map(input=>[input.dataset.weight,Number(input.value)]));const d=await(await fetch('/api/profile/custom',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({weights})})).json();statusEl.textContent=d.ok?'Custom 权重已保存并重新排序':d.error}
document.getElementById('settingsButton').onclick=loadPlugins;document.getElementById('closeSettings').onclick=()=>settingsPanel.classList.remove('open');pluginList.onchange=async e=>{const id=e.target.dataset.plugin;if(!id)return;e.target.disabled=true;const d=await(await fetch('/api/plugin',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({plugin_id:id,enabled:e.target.checked})})).json();statusEl.textContent=d.ok?(e.target.checked?'标准已启用，点击“生成 Top 5”补充分析':'标准已关闭'):d.error;if(!d.ok)e.target.checked=!e.target.checked;e.target.disabled=false};
const finishPanel=document.getElementById('finishPanel'),finishResult=document.getElementById('finishResult'),copySelection=document.getElementById('copySelection'),xmpSelection=document.getElementById('xmpSelection');
async function showFinish(){const d=await(await fetch('/api/completion')).json();document.getElementById('finishKept').textContent=d.selected_count;document.getElementById('finishRejected').textContent=d.rejected_count;document.getElementById('finishUnreviewed').textContent=d.unreviewed_count;let overview=document.getElementById('finishOverview');if(!overview){overview=document.createElement('div');overview.id='finishOverview';overview.className='finish-overview';document.querySelector('.finish-actions').before(overview)}const metricLabels={sharpness:'清晰度',exposure:'曝光',highlights:'高光保留',shadows:'阴影保留',contrast:'对比',color:'色彩',white_balance:'白平衡',composition:'构图'};const strengths=Object.entries(d.average_metrics||{}).sort((a,b)=>b[1]-a[1]).slice(0,3).map(([key,value])=>`${metricLabels[key]} ${Math.round(value)}`).join(' · ');overview.innerHTML=`${d.selected_items.length?`<div class="finish-strip">${d.selected_items.map(item=>`<div class="finish-thumb"><img src="${item.thumbnail}" alt="${escapeHTML(item.name)}"><span>${escapeHTML(item.name)}</span></div>`).join('')}</div>`:''}<div class="finish-insight"><div><strong>${escapeHTML(d.profile_name)} 标准 · 完成 ${d.completion_percent}%</strong>已评价 ${d.reviewed_count} / ${d.reviewed_count+d.unreviewed_count} 张${strengths?`；保留组优势集中在 ${strengths}。`:''}</div><div><strong>${d.model.ready?'个人偏好已经参与排序':'偏好仍在学习中'}</strong>${d.model.ready?`基于 ${d.model.training_count} 条反馈，当前影响推荐 ${d.model.influence}%。`:'至少积累 2 张保留和 2 张淘汰后，模型会开始个性化排序。'}</div></div>`;copySelection.disabled=xmpSelection.disabled=d.selected_count===0;finishResult.textContent=d.selected_count?'选择一种交付方式，之后仍可继续选片。':'请先至少保留一张照片。';finishPanel.classList.add('open')}
async function finalize(action,destination){copySelection.disabled=xmpSelection.disabled=true;finishResult.textContent=action==='copy'?'正在复制保留的原始照片…':'正在生成 XMP sidecar…';try{const d=await(await fetch('/api/finalize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,destination})})).json();finishResult.textContent=d.ok?`${action==='copy'?'已复制':'已生成'} ${d.created} 个文件${d.conflicts?`，跳过 ${d.conflicts} 个同名冲突`:''}。审计记录：${d.audit}`:d.error||'操作失败'}finally{copySelection.disabled=xmpSelection.disabled=false}}
document.getElementById('finishButton').onclick=showFinish;document.getElementById('closeFinish').onclick=()=>finishPanel.classList.remove('open');copySelection.onclick=async()=>{const d=await(await fetch('/api/folder-picker',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({purpose:'export'})})).json();if(d.ok)await finalize('copy',d.folder);else if(!d.cancelled)finishResult.textContent=d.error};xmpSelection.onclick=()=>finalize('xmp');
loadProfiles();loadCandidates();
</script></html>"""
