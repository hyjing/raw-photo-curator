import html
import json
from pathlib import Path

from .models import Result


def write_report(results: list[Result], output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    (output / "results.json").write_text(
        json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    cards = []
    for item in results:
        cards.append(f"""
        <article><img src="{html.escape(item.thumbnail)}" loading="lazy">
        <div class="body"><h2>{html.escape(item.path.name)}</h2>
        <p><b>保留 {item.keep_score:.0f}</b><b>调色 {item.edit_score:.0f}</b></p>
        <small>{html.escape(' · '.join(item.notes))}</small>
        <details><summary>详细指标</summary><pre>{html.escape(json.dumps(item.metrics.__dict__, ensure_ascii=False, indent=2))}</pre></details>
        </div></article>""")
    page = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
    <meta name="viewport" content="width=device-width"><title>RAW Photo Curator</title>
    <style>body{{font:15px system-ui;margin:0;background:#111;color:#eee}}header{{padding:28px 4vw}}
    main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:18px;padding:0 4vw 40px}}
    article{{background:#202020;border-radius:12px;overflow:hidden}}img{{width:100%;aspect-ratio:3/2;object-fit:cover}}
    .body{{padding:14px}}h2{{font-size:15px;margin:0 0 10px;overflow-wrap:anywhere}}p{{display:flex;gap:18px}}
    b:first-child{{color:#77dd9a}}b:last-child{{color:#82b8ff}}small{{color:#bbb}}pre{{white-space:pre-wrap}}</style>
    <header><h1>RAW Photo Curator</h1><p>{len(results)} 张照片 · 按保留分排序</p></header>
    <main>{''.join(cards)}</main></html>"""
    destination = output / "index.html"
    destination.write_text(page, encoding="utf-8")
    return destination

