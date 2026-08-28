import json
import mimetypes
import sqlite3
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .models import Result

CHOICES = {"keep", "edit", "reject", "maybe", None}


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
    with _connect(database) as connection:
        saved = {row["path"]: dict(row) for row in connection.execute("SELECT * FROM feedback")}
    payload = []
    for index, result in enumerate(results):
        item = result.to_dict()
        item["id"] = index
        item["thumbnail"] = f"/thumbnails/{Path(result.thumbnail).name}"
        feedback = saved.get(str(result.path))
        if feedback:
            feedback["tags"] = json.loads(feedback["tags"])
        item["feedback"] = feedback
        payload.append(item)
    return payload


def _summary(database: Path) -> dict[str, int]:
    counts = {"keep": 0, "edit": 0, "reject": 0, "maybe": 0, "reviewed": 0}
    with _connect(database) as connection:
        for row in connection.execute("SELECT choice, COUNT(*) count FROM feedback GROUP BY choice"):
            if row["choice"] in counts:
                counts[row["choice"]] = row["count"]
            counts["reviewed"] += row["count"]
    return counts


def make_handler(results: list[Result], output: Path, database: Path):
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
                self._json({"photos": _photo_payload(results, database), "summary": _summary(database)})
            elif route.startswith("/thumbnails/"):
                name = Path(route).name
                candidate = output / "thumbnails" / name
                if candidate.is_file() and candidate.parent.resolve() == (output / "thumbnails").resolve():
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
            if urlparse(self.path).path != "/api/feedback":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65_536:
                    raise ValueError("invalid body size")
                data = json.loads(self.rfile.read(size))
                photo_id = int(data["id"])
                result = results[photo_id]
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
                self._json({"ok": True, "summary": _summary(database)})
            except (ValueError, KeyError, IndexError, json.JSONDecodeError):
                self._json({"ok": False, "error": "invalid feedback"}, HTTPStatus.BAD_REQUEST)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def serve(results: list[Result], output: Path, port: int = 8765) -> None:
    output.mkdir(parents=True, exist_ok=True)
    database = output / "feedback.sqlite3"
    _connect(database).close()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(results, output, database))
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
header{height:58px;display:flex;align-items:center;gap:20px;padding:0 24px;border-bottom:1px solid #292b30}
header b{font-size:17px}#summary{color:#a6a8ad}.layout{height:calc(100vh - 58px);display:grid;grid-template-columns:minmax(0,1fr) 340px}
.stage{display:flex;align-items:center;justify-content:center;padding:24px;background:#08090a;overflow:hidden}.stage img{max-width:100%;max-height:100%;object-fit:contain}
aside{padding:20px;overflow:auto;border-left:1px solid #292b30}h1{font-size:18px;margin:0 0 6px;overflow-wrap:anywhere}.path{font-size:12px;color:#777;overflow-wrap:anywhere}
.scores{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:18px 0}.score{padding:13px;border-radius:10px;background:#1b1d21}.score strong{display:block;font-size:26px}.keep strong{color:#64dc90}.edit strong{color:#6dbaff}
.metrics{display:grid;grid-template-columns:1fr 1fr;gap:8px}.metric{background:#15171a;padding:9px;border-radius:8px}.metric span{display:block;color:#888;font-size:12px}.metric b{font-size:18px}
.choices,.stars,.tags{display:flex;gap:7px;flex-wrap:wrap;margin:16px 0}button{border:1px solid #444;background:#202228;color:#eee;border-radius:8px;padding:9px 11px;cursor:pointer}button:hover{background:#30333a}button.active{background:#17613a;border-color:#45ce7b}.reject.active{background:#792727;border-color:#ee6868}.edit.active{background:#185987;border-color:#61baff}
.stars button{font-size:19px;padding:5px 8px}.tags button{font-size:12px;padding:6px 8px}textarea{width:100%;background:#15171a;color:#eee;border:1px solid #3b3e44;border-radius:8px;padding:9px;resize:vertical}.nav{display:flex;justify-content:space-between;margin-top:16px}.hint{color:#73767d;font-size:12px;line-height:1.6;margin-top:16px}
@media(max-width:800px){.layout{height:auto;grid-template-columns:1fr}.stage{height:55vh}aside{border-left:0}.path{display:none}}
</style><header><b>RAW Photo Curator</b><span id="position"></span><span id="summary">载入中…</span></header>
<div class="layout"><div class="stage"><img id="photo"></div><aside><h1 id="name"></h1><div class="path" id="path"></div>
<div class="scores"><div class="score keep">保留分<strong id="keep"></strong></div><div class="score edit">调色潜力<strong id="edit"></strong></div></div>
<div class="metrics" id="metrics"></div><div class="choices"><button data-choice="keep">P 保留</button><button class="edit" data-choice="edit">E 调色</button><button data-choice="maybe">M 待定</button><button class="reject" data-choice="reject">X 淘汰</button></div>
<div>星级</div><div class="stars" id="stars"></div><div>原因标签</div><div class="tags" id="tags"></div>
<textarea id="note" rows="3" maxlength="300" placeholder="备注会自动保存"></textarea><div class="nav"><button id="prev">← 上一张</button><button id="next">下一张 →</button></div>
<div class="hint">快捷键：P 保留 · E 调色 · M 待定 · X 淘汰 · 1–5 星 · ←/→ 切换<br>客观分来自预览像素的清晰度、曝光、高光、阴影、色彩与画面区域信息。</div></aside></div>
<script>
let photos=[], index=0, timer; const labels={sharpness:'清晰度',exposure:'曝光',highlights:'高光保留',shadows:'阴影保留',color:'色彩信息',composition:'构图代理'};
const tagNames=['构图','光线','清晰度','表情','色彩','景深'];
async function load(){const r=await fetch('/api/photos');const d=await r.json();photos=d.photos;showSummary(d.summary);render()}
function current(){return photos[index]} function fb(){return current().feedback ||= {choice:null,rating:null,tags:[],note:''}}
function render(){const p=current();if(!p)return;photo.src=p.thumbnail;name.textContent=p.path.split('/').pop();path.textContent=p.path;keep.textContent=p.keep_score;edit.textContent=p.edit_score;position.textContent=`${index+1} / ${photos.length}`;
 metrics.innerHTML=Object.entries(p.metrics).map(([k,v])=>`<div class="metric"><span>${labels[k]}</span><b>${v}</b></div>`).join('');
 document.querySelectorAll('[data-choice]').forEach(b=>b.classList.toggle('active',b.dataset.choice===fb().choice));stars.innerHTML=[1,2,3,4,5].map(n=>`<button data-rating="${n}" class="${fb().rating>=n?'active':''}">★</button>`).join('');
 tags.innerHTML=tagNames.map(t=>`<button data-tag="${t}" class="${fb().tags.includes(t)?'active':''}">${t}</button>`).join('');note.value=fb().note||''}
async function save(){const p=current(), f=fb();const r=await fetch('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:p.id,...f})});const d=await r.json();if(d.ok)showSummary(d.summary)}
function showSummary(s){summary.textContent=`已评 ${s.reviewed} · 保留 ${s.keep} · 调色 ${s.edit} · 待定 ${s.maybe} · 淘汰 ${s.reject}`}
function choose(c){fb().choice=fb().choice===c?null:c;render();save()} function move(n){index=Math.max(0,Math.min(photos.length-1,index+n));render()}
document.querySelector('.choices').onclick=e=>{if(e.target.dataset.choice)choose(e.target.dataset.choice)};
stars.onclick=e=>{if(e.target.dataset.rating){fb().rating=Number(e.target.dataset.rating);render();save()}};
tags.onclick=e=>{const t=e.target.dataset.tag;if(t){fb().tags=fb().tags.includes(t)?fb().tags.filter(x=>x!==t):[...fb().tags,t];render();save()}};
note.oninput=()=>{fb().note=note.value;clearTimeout(timer);timer=setTimeout(save,350)};
document.getElementById('prev').onclick=()=>move(-1);
document.getElementById('next').onclick=()=>move(1);
document.onkeydown=e=>{if(e.target===note)return;if(e.key==='ArrowRight')move(1);if(e.key==='ArrowLeft')move(-1);if('pPeEmMxX'.includes(e.key))choose(({p:'keep',e:'edit',m:'maybe',x:'reject'})[e.key.toLowerCase()]);if(/[1-5]/.test(e.key)){fb().rating=Number(e.key);render();save()}};load();
</script></html>"""
