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
    for index, item in enumerate(results):
        cards.append(f"""
        <article data-id="{index}" data-path="{html.escape(str(item.path), quote=True)}"
          data-keep="{item.keep_score}" data-edit="{item.edit_score}">
        <img src="{html.escape(item.thumbnail)}" loading="lazy">
        <div class="body"><h2>{html.escape(item.path.name)}</h2>
        <p><b>保留 {item.keep_score:.0f}</b><b>调色 {item.edit_score:.0f}</b></p>
        <small>{html.escape(' · '.join(item.notes))}</small>
        <div class="feedback" role="group" aria-label="照片反馈">
          <button data-choice="keep">✓ 保留</button>
          <button data-choice="edit">◐ 值得调色</button>
          <button data-choice="reject">× 淘汰</button>
        </div>
        <textarea rows="2" maxlength="300" placeholder="可选备注，例如：构图喜欢，但人物闭眼"></textarea>
        <details><summary>详细指标</summary><pre>{html.escape(json.dumps(item.metrics.__dict__, ensure_ascii=False, indent=2))}</pre></details>
        </div></article>""")
    page = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
    <meta name="viewport" content="width=device-width"><title>RAW Photo Curator</title>
    <style>body{{font:15px system-ui;margin:0;background:#111;color:#eee}}header{{padding:28px 4vw;position:sticky;top:0;background:#111e;backdrop-filter:blur(12px);z-index:2}}
    main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:18px;padding:0 4vw 40px}}
    article{{background:#202020;border-radius:12px;overflow:hidden}}img{{width:100%;aspect-ratio:3/2;object-fit:cover}}
    .body{{padding:14px}}h2{{font-size:15px;margin:0 0 10px;overflow-wrap:anywhere}}p{{display:flex;gap:18px}}
    b:first-child{{color:#77dd9a}}b:last-child{{color:#82b8ff}}small{{color:#bbb}}pre{{white-space:pre-wrap}}
    .feedback{{display:flex;gap:6px;margin:14px 0 9px}}button{{background:#333;color:#eee;border:1px solid #555;border-radius:8px;padding:8px;cursor:pointer}}
    button:hover{{background:#444}}button.active[data-choice=keep]{{background:#18733a;border-color:#43c873}}
    button.active[data-choice=edit]{{background:#185b91;border-color:#5fb7fa}}button.active[data-choice=reject]{{background:#8b2929;border-color:#ed7070}}
    textarea{{box-sizing:border-box;width:100%;resize:vertical;background:#171717;color:#eee;border:1px solid #444;border-radius:8px;padding:8px}}
    #export{{background:#eee;color:#111;border:0;font-weight:650}}#summary{{color:#bbb;margin-right:12px}}</style>
    <header><h1>RAW Photo Curator</h1><p>{len(results)} 张照片 · 按保留分排序</p>
      <span id="summary">尚未反馈</span><button id="export">导出反馈 JSON</button>
    </header><main>{''.join(cards)}</main>
    <script>
    const key = 'raw-photo-curator-feedback-v1';
    let feedback = {{}};
    try {{ feedback = JSON.parse(localStorage.getItem(key) || '{{}}'); }} catch (_) {{}}
    const cards = [...document.querySelectorAll('article[data-id]')];
    function render(card) {{
      const value = feedback[card.dataset.path] || {{}};
      card.querySelectorAll('[data-choice]').forEach(b => b.classList.toggle('active', b.dataset.choice === value.choice));
      card.querySelector('textarea').value = value.note || '';
    }}
    function save(card, choice, note) {{
      feedback[card.dataset.path] = {{
        choice, note, keep_score: Number(card.dataset.keep), edit_score: Number(card.dataset.edit),
        updated_at: new Date().toISOString()
      }};
      localStorage.setItem(key, JSON.stringify(feedback)); updateSummary();
    }}
    function updateSummary() {{
      const values = Object.values(feedback);
      const count = c => values.filter(v => v.choice === c).length;
      document.querySelector('#summary').textContent = `已反馈 ${{values.length}} · 保留 ${{count('keep')}} · 调色 ${{count('edit')}} · 淘汰 ${{count('reject')}}`;
    }}
    cards.forEach(card => {{
      render(card);
      card.querySelectorAll('[data-choice]').forEach(button => button.addEventListener('click', () => {{
        const old = feedback[card.dataset.path]?.choice;
        save(card, old === button.dataset.choice ? null : button.dataset.choice, card.querySelector('textarea').value);
        render(card);
      }}));
      card.querySelector('textarea').addEventListener('change', event => save(card, feedback[card.dataset.path]?.choice || null, event.target.value.trim()));
    }});
    document.querySelector('#export').addEventListener('click', () => {{
      const payload = Object.entries(feedback).map(([path, value]) => ({{path, ...value}}));
      const blob = new Blob([JSON.stringify(payload, null, 2)], {{type: 'application/json'}});
      const link = Object.assign(document.createElement('a'), {{href: URL.createObjectURL(blob), download: 'raw-curator-feedback.json'}});
      link.click(); URL.revokeObjectURL(link.href);
    }});
    updateSummary();
    </script></html>"""
    destination = output / "index.html"
    destination.write_text(page, encoding="utf-8")
    return destination
