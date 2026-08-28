import json
import mimetypes
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from threading import Event, Lock, Thread
from urllib.parse import parse_qs, urlparse

from PIL import Image

from .catalog import Catalog
from .models import Result
from .profiles import BUILTIN_PROFILES, Profile, hard_rule_reasons, weighted_score
from .recommendation import recommendation_scores

CHOICES = {"keep", "edit", "reject", "maybe", None}


@dataclass
class SessionState:
    results: list[Result]
    output: Path
    folder: Path
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
    for profile in BUILTIN_PROFILES:
        connection.execute(
            """INSERT OR IGNORE INTO profiles
            (id, name, weights, hard_rules, enabled_plugins, is_builtin)
            VALUES (?, ?, ?, ?, ?, 1)""",
            (
                profile.id,
                profile.name,
                json.dumps(profile.weights),
                json.dumps(profile.hard_rules),
                json.dumps(profile.enabled_plugins),
            ),
        )
    connection.execute(
        "INSERT OR IGNORE INTO app_settings(key, value) VALUES ('active_profile', 'travel')"
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


def _read_feedback(database: Path) -> dict[str, dict]:
    with _connect(database) as connection:
        return {row["path"]: dict(row) for row in connection.execute("SELECT * FROM feedback")}


def _refresh_candidates(state: SessionState, database: Path) -> None:
    feedback = _read_feedback(database)
    profile = _active_profile(database)
    priors = {str(result.path): weighted_score(result, profile) for result in state.results}
    scores = recommendation_scores(state.results, feedback, priors)
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
    available = sorted(
        (
            result
            for result in state.results
            if str(result.path) not in excluded and not hard_rule_reasons(result, profile)
        ),
        key=lambda result: scores[str(result.path)],
        reverse=True,
    )
    retained = kept_current + active_unreviewed
    needed = 5 - len(retained)
    state.candidate_paths = retained + [str(result.path) for result in available[:needed]]


def _candidate_payload(state: SessionState, database: Path) -> list[dict]:
    feedback = _read_feedback(database)
    with _connect(database) as connection:
        rotations = {row["path"]: row["degrees"] for row in connection.execute("SELECT * FROM rotations")}
    profile = _active_profile(database)
    priors = {str(result.path): weighted_score(result, profile) for result in state.results}
    scores = recommendation_scores(state.results, feedback, priors)
    by_path = {str(result.path): result for result in state.results}
    output = []
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
        item["kept"] = feedback.get(path, {}).get("choice") == "keep"
        item["rotation"] = rotation
        output.append(item)
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
                    "folder": str(state.folder),
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
                    "folder": str(state.folder),
                    "round": state.round_number,
                    "summary": _summary(database, state.results),
                    "active_profile": _active_profile(database).id,
                })
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
                thumbnails = {
                    str(result.path): f"/thumbnails/{Path(result.thumbnail).name}"
                    for result in state.results
                }
                for group in groups:
                    for member in group["members"]:
                        thumbnail = thumbnails.get(member["path"])
                        if not thumbnail and member.get("thumbnail"):
                            thumbnail = f"/thumbnails/{Path(member['thumbnail']).name}"
                        member["thumbnail"] = thumbnail
                        member["decision"] = group_feedback.get(
                            (group["id"], member["id"])
                        )
                self._json({"groups": groups})
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
            if route == "/api/groups/correct":
                self._correct_groups()
                return
            if route == "/api/group-feedback":
                self._save_group_feedback()
                return
            if route == "/api/rotation":
                self._rotate_photo()
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
                with _connect(database) as connection:
                    connection.execute(
                        """INSERT INTO feedback(path, choice, rating, tags, note, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(path) DO UPDATE SET choice=excluded.choice,
                        rating=excluded.rating, tags=excluded.tags, note=excluded.note,
                        updated_at=excluded.updated_at""",
                        (str(result.path), choice, rating, json.dumps(tags[:8]), note, now),
                    )
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

        def _load_more(self) -> None:
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
                requested = Path(str(data.get("folder", state.folder))).expanduser().resolve()
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
                feedback = _read_feedback(database)
                profile = _active_profile(database)
                priors = {
                    str(result.path): weighted_score(result, profile)
                    for result in all_results
                }
                scores = recommendation_scores(all_results, feedback, priors)
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

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def serve(results: list[Result], output: Path, port: int, folder: Path, limit: int, analyzer: object) -> None:
    output.mkdir(parents=True, exist_ok=True)
    database = output / "feedback.sqlite3"
    _connect(database).close()
    state = SessionState(results, output, folder.resolve(), limit, analyzer)
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
.controls{display:grid;grid-template-columns:minmax(280px,1fr) auto auto;gap:10px}.pathbox{display:flex;align-items:center;gap:9px;padding:0 13px;border:1px solid var(--line);border-radius:12px;background:#0d1016;transition:.2s}.pathbox:focus-within{border-color:#65d998;box-shadow:0 0 0 3px #65d99814}.pathbox span{color:#697181}input{width:100%;border:0;outline:0;background:transparent;color:var(--text);padding:12px 0}select,button{border:1px solid var(--line);background:#20252f;color:var(--text);border-radius:11px;padding:10px 14px;cursor:pointer;transition:.18s}button:hover{border-color:#4c5565;transform:translateY(-1px)}button:disabled{opacity:.5;cursor:default;transform:none}#start{background:var(--text);color:#0b0d11;border:0;font-weight:750;padding-inline:20px}.activity{display:flex;align-items:center;gap:12px;min-height:20px;margin-top:10px}progress{width:180px;height:5px;border:0;accent-color:var(--accent)}#status{color:var(--muted);font-size:12px}
main{max-width:1540px;margin:auto;padding:28px 4vw 60px}.grid{display:grid;grid-template-columns:1fr;gap:26px}.card{position:relative;display:grid;grid-template-columns:minmax(0,1.8fr) minmax(350px,.82fr);background:linear-gradient(145deg,#171b23,#11141a);border:1px solid var(--line);border-radius:20px;overflow:hidden;box-shadow:0 18px 60px #0005}.card.kept{border-color:#56d88d;box-shadow:0 18px 60px #0005,0 0 0 1px #56d88d55}.rank{position:absolute;z-index:2;top:14px;left:14px;padding:7px 10px;border:1px solid #ffffff26;border-radius:10px;background:#07090ba8;backdrop-filter:blur(12px);font-weight:800}.photo-wrap{position:relative;display:grid;place-items:center;min-height:470px;background:linear-gradient(145deg,#08090c,#0e1116)}.card img{display:block;width:100%;height:100%;max-height:72vh;object-fit:contain}.body{display:flex;flex-direction:column;padding:23px}.eyebrow{color:var(--accent);font-size:11px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}.title-row{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:5px}.name{font-size:18px;font-weight:750;overflow-wrap:anywhere}.rotate{position:absolute;z-index:2;top:14px;right:14px;padding:9px 13px;white-space:nowrap;background:#07090bc7;border-color:#ffffff2b;backdrop-filter:blur(12px);box-shadow:0 5px 18px #0006}.rotate:hover{background:#20252ee8}.score{display:flex;gap:9px;margin:16px 0 12px}.score span{display:flex;flex-direction:column;gap:2px;flex:1;padding:10px 12px;border:1px solid var(--line);border-radius:12px;color:var(--muted);font-size:11px;background:#0b0e13}.score strong{font-size:22px;color:var(--text);line-height:1.1}.reason{min-height:44px;color:#c8ccd4;line-height:1.55}.radar{display:block;width:100%;max-width:280px;margin:auto}.radar .gridline{fill:none;stroke:#343a46;stroke-width:1}.radar .axis{stroke:#2b303a}.radar .shape{fill:#72eca535;stroke:#72eca5;stroke-width:2}.radar text{fill:#aeb4bf;font:11px system-ui}.actions{display:grid;grid-template-columns:1.25fr 1fr;gap:9px;margin-top:auto;padding-top:14px}.actions button{font-weight:700}.keep{background:#22633e;border-color:#388d5b}.keep:hover{background:#2b784b}.reject{background:#302328;border-color:#5a343d;color:#ffc4c4}.reject:hover{background:#48282f}.badge{color:var(--accent);font-size:12px;margin-top:9px}
@media(max-width:900px){header{position:relative}.card{grid-template-columns:1fr}.photo-wrap{min-height:0}.card img{max-height:none;aspect-ratio:3/2}.radar{max-width:250px}}@media(max-width:640px){.controls{grid-template-columns:1fr}.topline{align-items:flex-start}.meta{flex-direction:column}.card{border-radius:15px}.body{padding:18px}}
.group-panel{position:fixed;inset:0;z-index:20;display:none;background:#07090be8;backdrop-filter:blur(16px);overflow:auto;padding:24px 4vw 60px}.group-panel.open{display:block}.group-toolbar{position:sticky;top:0;z-index:3;display:flex;align-items:center;gap:12px;padding:12px 0 18px;background:#07090be8}.group-toolbar h2{margin:0 auto 0 0}.group-list{display:grid;gap:22px}.similarity-group{padding:18px;border:1px solid var(--line);border-radius:18px;background:var(--surface)}.group-head{display:flex;gap:10px;align-items:center;margin-bottom:13px}.group-head strong{margin-right:auto}.member-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}.member{position:relative;overflow:hidden;border-radius:12px;background:#050608;aspect-ratio:3/2}.member img{width:100%;height:100%;object-fit:contain;transform:scale(var(--group-zoom,1));transition:transform .15s}.member label{position:absolute;left:8px;bottom:8px;padding:5px 8px;border-radius:8px;background:#050608cc;font-size:11px}.member input{width:auto}.member-actions{position:absolute;display:flex;gap:5px;right:7px;top:7px}.member-actions button{padding:5px 7px;font-size:11px;background:#080a0dcc}.member-actions .chosen{border-color:var(--accent);color:var(--accent)}.split{margin-top:12px}
</style><header><div class="topline"><div class="brand"><div class="mark">R</div><div><h1>RAW Photo Curator</h1><small>Local-first photo selection</small></div></div><div class="meta"><span class="chip" id="round">第 1 轮</span><span class="chip" id="summary">尚未选择</span></div></div><div class="controls"><label class="pathbox"><span>⌁</span><input id="folder" aria-label="本地照片文件夹" placeholder="输入包含 ARW / JPG 的照片文件夹"></label><select id="profile" aria-label="选片标准"></select><div><button id="groupsButton">相似组</button> <button id="cancel" hidden>取消</button> <button id="start">生成 Top 5</button></div></div><div class="activity"><progress id="progress" value="0" max="100" hidden></progress><span id="status">输入本地照片路径，分析结果只保存在这台设备</span></div></header>
<main><div class="grid" id="grid"></div></main><section class="group-panel" id="groupPanel"><div class="group-toolbar"><h2>相似照片与连拍组</h2><label>同步缩放 <input id="groupZoom" type="range" min="1" max="3" step=".1" value="1"></label><button id="mergeGroups">合并选中组</button><button id="closeGroups">关闭</button></div><div class="group-list" id="groupList"></div></section>
<script>
const folder=document.getElementById('folder'),profile=document.getElementById('profile'),start=document.getElementById('start'),cancel=document.getElementById('cancel'),bar=document.getElementById('progress'),statusEl=document.getElementById('status'),grid=document.getElementById('grid'),roundEl=document.getElementById('round'),summaryEl=document.getElementById('summary');
const groupPanel=document.getElementById('groupPanel'),groupList=document.getElementById('groupList');let groupsData=[];
const escapeHTML=value=>String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
async function loadCandidates(){const d=await(await fetch('/api/candidates')).json();folder.value=d.folder;roundEl.textContent=`第 ${d.round} 轮`;summaryEl.textContent=`累计保留 ${d.summary.keep} · 淘汰 ${d.summary.reject}`;render(d.candidates)}
async function loadProfiles(){const d=await(await fetch('/api/profiles')).json();profile.innerHTML=d.profiles.map(p=>`<option value="${p.id}">${escapeHTML(p.name)}</option>`).join('');profile.value=d.active_profile}
const axes=[['清晰',m=>m.sharpness],['曝光',m=>m.exposure],['动态范围',m=>(m.highlights+m.shadows)/2],['对比',m=>m.contrast],['色彩',m=>(m.color+m.white_balance)/2],['构图',m=>m.composition]];
function polygon(values,radius){return values.map((value,i)=>{const angle=-Math.PI/2+i*Math.PI/3,r=radius*value/100;return `${110+Math.cos(angle)*r},${100+Math.sin(angle)*r}`}).join(' ')}
function radar(metrics){const values=axes.map(axis=>Math.round(axis[1](metrics)));const grids=[25,50,75,100].map(level=>`<polygon class="gridline" points="${polygon(Array(6).fill(level),72)}"/>`).join('');const lines=axes.map((_,i)=>{const a=-Math.PI/2+i*Math.PI/3;return `<line class="axis" x1="110" y1="100" x2="${110+Math.cos(a)*72}" y2="${100+Math.sin(a)*72}"/>`}).join('');const labels=axes.map((axis,i)=>{const a=-Math.PI/2+i*Math.PI/3,x=110+Math.cos(a)*91,y=104+Math.sin(a)*86;return `<text x="${x}" y="${y}" text-anchor="middle">${axis[0]} ${values[i]}</text>`}).join('');return `<svg class="radar" viewBox="0 0 220 200" role="img" aria-label="六项照片评分"><title>六项照片评分</title>${grids}${lines}<polygon class="shape" points="${polygon(values,72)}"/>${labels}</svg>`}
const explanations={清晰:'主体细节清楚，焦点表现较可靠。',曝光:'整体曝光均衡，中间调保留自然。',动态范围:'高光和暗部保留完整，后期调整空间较大。',对比:'明暗层次清楚，画面结构比较突出。',色彩:'色彩关系协调，整体观感较自然。',构图:'视觉信息集中，画面边缘干扰较少。'};
function reason(metrics){const best=axes.map(axis=>[axis[0],axis[1](metrics)]).sort((a,b)=>b[1]-a[1]).slice(0,2);return best.map(item=>explanations[item[0]]).join(' ')}
function render(items){grid.innerHTML=items.map((p,i)=>{const name=escapeHTML(p.path.split('/').pop());return `<article class="card ${p.kept?'kept':''}"><div class="rank">#${i+1}</div><div class="photo-wrap"><img src="${p.thumbnail}" alt="${name}"><button class="rotate" title="顺时针旋转 90°" aria-label="旋转 ${name}" data-path="${encodeURIComponent(p.path)}" data-rotate="true">↻ 旋转 90°</button></div><div class="body"><div class="eyebrow">Top recommendation</div><div class="title-row"><div class="name">${name}</div></div><div class="score"><span>个人推荐<strong>${p.recommendation_score}</strong></span><span>客观质量<strong>${p.keep_score}</strong></span></div><div class="reason">${reason(p.metrics)}</div>${radar(p.metrics)}<div class="actions"><button class="keep" data-path="${encodeURIComponent(p.path)}" data-choice="keep">✓ 保留</button><button class="reject" data-path="${encodeURIComponent(p.path)}" data-choice="reject">淘汰</button></div>${p.kept?'<div class="badge">已加入本轮候选</div>':''}</div></article>`}).join('')}
grid.onclick=async e=>{const pathValue=e.target.dataset.path;if(!pathValue)return;const path=decodeURIComponent(pathValue);if(e.target.dataset.rotate){e.target.disabled=true;const d=await(await fetch('/api/rotation',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})})).json();if(d.ok){render(d.candidates);statusEl.textContent=`预览已旋转 ${d.degrees}°`}return}const choice=e.target.dataset.choice;if(!choice)return;e.target.disabled=true;const before=roundEl.textContent;const d=await(await fetch('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path,choice})})).json();if(d.ok){roundEl.textContent=`第 ${d.round} 轮`;summaryEl.textContent=`累计保留 ${d.summary.keep} · 淘汰 ${d.summary.reject}`;render(d.candidates);statusEl.textContent=before!==roundEl.textContent?'已选满 5 张，偏好模型已更新并生成新一轮':'偏好模型已更新'}};
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function followJob(){while(true){const p=await(await fetch('/api/progress')).json();if(p.total)bar.value=p.current/p.total*100;if(p.stage==='ranking')statusEl.textContent='正在根据反馈生成 Top 5…';else if(p.running)statusEl.textContent=`正在分析 ${p.current} / ${p.total}（${p.total?Math.round(p.current/p.total*100):0}%）`;if(!p.running){if(p.stage==='done'){await loadCandidates();statusEl.textContent=`已分析 ${p.total} 张 · 缓存 ${p.cache_hits} · 新分析 ${p.cache_misses}${p.failed?` · 跳过 ${p.failed}`:''}`}else if(p.stage==='cancelled')statusEl.textContent='已取消；下次会从缓存进度继续';else statusEl.textContent=p.error||'分析失败';break}await wait(350)}}
start.onclick=async()=>{start.disabled=true;cancel.disabled=false;cancel.hidden=false;bar.hidden=false;bar.value=0;statusEl.textContent='正在建立本地照片索引…';try{const d=await(await fetch('/api/recommendations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folder:folder.value})})).json();if(d.ok)await followJob();else statusEl.textContent=d.error||'分析失败'}finally{bar.value=100;setTimeout(()=>bar.hidden=true,1000);cancel.hidden=true;start.disabled=false}};
cancel.onclick=async()=>{cancel.disabled=true;await fetch('/api/cancel',{method:'POST'});statusEl.textContent='正在安全停止…'};
profile.onchange=async()=>{profile.disabled=true;const d=await(await fetch('/api/profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile_id:profile.value})})).json();if(d.ok){render(d.candidates);statusEl.textContent=`已切换到 ${profile.options[profile.selectedIndex].text} 标准并重新排序`}profile.disabled=false};
async function loadGroups(){const d=await(await fetch('/api/groups')).json();groupsData=d.groups;groupList.innerHTML=groupsData.map(g=>`<article class="similarity-group" data-group="${g.id}"><div class="group-head"><input type="checkbox" data-select-group><strong>${g.type==='duplicate'?'近似重复':'连拍'} · ${g.members.length} 张</strong><span>置信度 ${Math.round(g.confidence*100)}%</span>${g.manually_corrected?'<span class="badge">人工纠正</span>':''}</div><div class="member-grid">${g.members.map(m=>`<div class="member"><img src="${m.thumbnail||''}" alt="${escapeHTML(m.path.split('/').pop())}"><div class="member-actions"><button class="${m.decision==='winner'?'chosen':''}" data-group-decision="winner" data-photo="${m.id}" data-feedback-group="${g.id}">最佳</button><button class="${m.decision==='reject'?'chosen':''}" data-group-decision="reject" data-photo="${m.id}" data-feedback-group="${g.id}">淘汰</button></div><label><input type="checkbox" data-member="${m.id}"> ${escapeHTML(m.path.split('/').pop())}</label></div>`).join('')}</div><button class="split" data-split="${g.id}">把勾选照片拆成新组</button></article>`).join('');groupPanel.classList.add('open')}
document.getElementById('groupsButton').onclick=loadGroups;document.getElementById('closeGroups').onclick=()=>groupPanel.classList.remove('open');document.getElementById('groupZoom').oninput=e=>groupPanel.style.setProperty('--group-zoom',e.target.value);
groupList.onclick=async e=>{if(e.target.dataset.groupDecision){await fetch('/api/group-feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({group_id:e.target.dataset.feedbackGroup,photo_id:e.target.dataset.photo,decision:e.target.dataset.groupDecision,reason_criteria:['group_comparison']})});await loadGroups();return}const id=e.target.dataset.split;if(!id)return;const article=e.target.closest('[data-group]'),selected=[...article.querySelectorAll('[data-member]:checked')].map(x=>x.dataset.member),all=[...article.querySelectorAll('[data-member]')].map(x=>x.dataset.member),rest=all.filter(x=>!selected.includes(x));if(!selected.length||!rest.length){statusEl.textContent='请勾选要拆出的照片';return}await fetch('/api/groups/correct',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source_group_ids:[id],partitions:[selected,rest],type:'related'})});await loadGroups()};
document.getElementById('mergeGroups').onclick=async()=>{const selected=[...groupList.querySelectorAll('[data-select-group]:checked')].map(x=>x.closest('[data-group]').dataset.group);if(selected.length<2){statusEl.textContent='请至少选择两个组';return}const members=groupsData.filter(g=>selected.includes(g.id)).flatMap(g=>g.members.map(m=>m.id));await fetch('/api/groups/correct',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source_group_ids:selected,partitions:[members],type:'related'})});await loadGroups()};
loadProfiles();loadCandidates();
</script></html>"""
