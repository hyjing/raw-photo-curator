import json
import mimetypes
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .models import Result
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
    return connection


def _photo_payload(results: list[Result], database: Path) -> list[dict]:
    saved = _read_feedback(database)
    personalized = recommendation_scores(results, saved)
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
            route = urlparse(self.path).path
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
                })
            elif route.startswith("/thumbnails/"):
                name = Path(route).name
                candidate = state.output / "thumbnails" / name
                if candidate.is_file() and candidate.parent.resolve() == (state.output / "thumbnails").resolve():
                    body = candidate.read_bytes()
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
            if route != "/api/feedback":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65_536:
                    raise ValueError("invalid body size")
                data = json.loads(self.rfile.read(size))
                photo_id = int(data["id"])
                result = state.results[photo_id]
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
                self._json({"ok": True, "summary": _summary(database, state.results)})
            except (ValueError, KeyError, IndexError, json.JSONDecodeError):
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
            state.progress_running = True
            state.progress_stage = "scanning"

            def update_progress(current: int, total: int) -> None:
                state.progress_current = current
                state.progress_total = total

            try:
                all_results = state.analyzer(
                    state.folder, state.output, None, 0, update_progress
                )
                state.progress_stage = "ranking"
                feedback = _read_feedback(database)
                scores = recommendation_scores(all_results, feedback)
                reviewed_paths = set(feedback)
                state.results = sorted(
                    all_results,
                    key=lambda result: (
                        str(result.path) in reviewed_paths,
                        -scores[str(result.path)],
                    ),
                )
                self._json({
                    "ok": True,
                    "count": len(state.results),
                    "feedback_count": len(feedback),
                })
            finally:
                state.progress_running = False
                state.progress_stage = "done"

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


APP_HTML = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
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
