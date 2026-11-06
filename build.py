#!/usr/bin/env python3
"""#REALITY歌ってみたYouTubeリスト HTML生成スクリプト。

data/musics.json（機械が集めた事実）に data/annotations.csv（人間の付箋）を
合成して、GitHub Pages 用の docs/index.html と docs/provided.html を作る。
サムネイルは YouTube の画像URLを直接表示するため、画像の保管は不要。

使い方: python3 build.py [--out docs]
確認:  python3 build.py --out /tmp/utattemita-preview
"""
import argparse
import csv
import html
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"


def load_annotations(path: Path) -> dict:
    out = {}
    if path.exists():
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("video_id"):
                    out[row["video_id"]] = row
    return out


def unescape_note(value: str) -> str:
    """annotations.csv では改行は文字どおり \\n と書く約束。表示時に改行に戻す。"""
    return (value or "").replace("\\n", "\n")


def merge(musics: list[dict], annotations: dict) -> list[dict]:
    """付箋を合成する。original / provided / credit / note は付箋が正。"""
    merged = []
    for m in musics:
        ann = annotations.get(m.get("video_id", ""), {})
        item = dict(m)
        if ann.get("original") == "1":
            item["original"] = "1"
        if ann.get("provided") == "1":
            item["provided"] = "1"
        if ann.get("credit"):
            item["credit"] = ann["credit"]
        if ann.get("note"):
            item["note"] = unescape_note(ann["note"])
        elif item.get("note"):
            item["note"] = unescape_note(item["note"])
        merged.append(item)
    return merged


def esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


def thumb(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{esc(video_id)}/mqdefault.jpg"


def row_html(item: dict, show_credit: bool = False) -> str:
    vid = esc(item.get("video_id", ""))
    badges = []
    if item.get("original") == "1":
        badges.append('<span class="badge orig">オリジナル</span>')
    if item.get("provided") == "1":
        badges.append('<span class="badge prov">楽曲提供</span>')
    links = []
    if item.get("reality_url"):
        links.append(f'<a href="{esc(item["reality_url"])}">REALITY</a>')
    if item.get("twitter_url"):
        links.append(f'<a href="{esc(item["twitter_url"])}">X</a>')
    if item.get("youtube_url"):
        links.append(f'<a href="{esc(item["youtube_url"])}">YouTube</a>')
    extra = ""
    if show_credit and item.get("credit"):
        extra += f'<div class="credit">{esc(unescape_note(item["credit"]))}</div>'
    if item.get("note"):
        extra += f'<div class="note">{esc(item["note"])}</div>'
    return (
        f'<article class="row" data-text="{esc((item.get("title", "") + " " + item.get("name", "")).lower())}">'
        f'<a href="https://www.youtube.com/watch?v={vid}" target="_blank" rel="noopener">'
        f'<img loading="lazy" src="{thumb(vid)}" alt=""></a>'
        f'<div class="body"><div class="title">'
        f'<a href="https://www.youtube.com/watch?v={vid}" target="_blank" rel="noopener">'
        f'{esc(item.get("title", ""))}</a> {" ".join(badges)}</div>'
        f'<div class="meta">{esc(item.get("name", ""))} ・ {esc(item.get("publish_date", ""))}'
        + (f' ・ {" ".join(links)}' if links else "")
        + f"</div>{extra}</div></article>"
    )


PAGE_CSS = """
body{font-family:system-ui,-apple-system,"Hiragino Kaku Gothic ProN",sans-serif;
max-width:960px;margin:0 auto;padding:16px;color:#222}
h1{font-size:20px} .sub{color:#666;font-size:13px}
.controls{position:sticky;top:0;background:#fff;padding:8px 0;display:flex;gap:8px;flex-wrap:wrap}
.controls input[type=search]{flex:1;min-width:200px;padding:8px;font-size:15px}
.controls button{padding:8px 12px;font-size:14px}
.controls button.on{background:#222;color:#fff}
.row{display:flex;gap:12px;padding:12px 0;border-top:1px solid #eee}
.row img{width:160px;height:90px;object-fit:cover;border-radius:6px}
.row .title{font-size:15px;line-height:1.4}
.row .meta{font-size:13px;color:#666;margin-top:4px}
.row .meta a{color:#666}
.row .note,.row .credit{font-size:13px;color:#444;white-space:pre-line;margin-top:4px}
.badge{font-size:11px;border-radius:4px;padding:1px 6px;margin-left:4px;white-space:nowrap}
.badge.orig{background:#e8f5e9;color:#2e7d32}
.badge.prov{background:#e3f2fd;color:#1565c0}
nav{font-size:14px;margin:8px 0}
"""

PAGE_JS = """
const q=document.getElementById('q'), rows=[...document.querySelectorAll('.row')];
let f='all';
function apply(){
  const t=q.value.trim().toLowerCase();
  let n=0;
  for(const r of rows){
    const okText=!t||r.dataset.text.includes(t);
    const okF=f==='all'||(f==='orig'&&r.querySelector('.orig'))||(f==='prov'&&r.querySelector('.prov'));
    r.style.display=(okText&&okF)?'':'none'; if(okText&&okF)n++;
  }
  document.getElementById('count').textContent=n+'件';
}
q.addEventListener('input',apply);
for(const b of document.querySelectorAll('[data-f]')){
  b.addEventListener('click',()=>{
    f=b.dataset.f;
    for(const x of document.querySelectorAll('[data-f]'))x.classList.toggle('on',x===b);
    apply();
  });
}
apply();
"""


def page(title: str, sub: str, rows: list[str], nav: str) -> str:
    body = "\n".join(rows)
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title><style>{PAGE_CSS}</style></head>
<body><h1>{esc(title)}</h1><p class="sub">{esc(sub)}</p><nav>{nav}</nav>
<div class="controls"><input id="q" type="search" placeholder="タイトル・名前で検索">
<button data-f="all" class="on">すべて</button>
<button data-f="orig">オリジナル</button>
<button data-f="prov">楽曲提供</button>
<span id="count"></span></div>
{body}
<script>{PAGE_JS}</script></body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(BASE / "docs"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    musics = json.load(open(DATA / "musics.json", encoding="utf-8"))
    annotations = load_annotations(DATA / "annotations.csv")
    merged = merge(musics, annotations)
    merged.sort(key=lambda m: (m.get("publish_date") or ""), reverse=True)
    # 念のため video_id で重複除去（collect.py 側でもキー管理しているが、
    # 移行データには重複が25件残っていたため。新しい方を残す）
    seen, deduped = set(), []
    for m in merged:
        if m.get("video_id") in seen:
            continue
        seen.add(m.get("video_id"))
        deduped.append(m)
    merged = deduped

    # 一覧：musics 全件（新しい順）
    rows = [row_html(m) for m in merged]
    (out / "index.html").write_text(
        page("#REALITY歌ってみたYouTubeリスト",
             f"全{len(merged)}件・新しい順。サムネイルとタイトルからYouTubeで開けます。"
             "「オリジナル」「楽曲提供」は annotations.csv の付箋による表示です。",
             rows,
             '<a href="provided.html">楽曲提供の一覧へ</a>'),
        encoding="utf-8")

    # 楽曲提供ページ：provided=1 の全件。musics に無い6件は付箋の情報で載せる。
    provided = [m for m in merged if m.get("provided") == "1"]
    musics_ids = {m["video_id"] for m in merged}
    missing = []
    for vid, ann in annotations.items():
        if ann.get("provided") == "1" and vid not in musics_ids:
            missing.append({
                "video_id": vid, "title": ann.get("title_hint", ""),
                "name": "", "publish_date": "", "provided": "1",
                "credit": ann.get("credit", ""), "note": unescape_note(ann.get("note", "")),
            })
    prows = [row_html(m, show_credit=True) for m in provided]
    prows += [row_html(m, show_credit=True) for m in missing]
    (out / "provided.html").write_text(
        page("楽曲提供",
             f"全{len(provided) + len(missing)}件（うち musics 未収録・追跡対象外の提供曲 {len(missing)}件）。"
             "クレジットは annotations.csv の付箋による表示です。",
             prows,
             '<a href="index.html">一覧へ戻る</a>'),
        encoding="utf-8")

    n_orig = sum(1 for m in merged if m.get("original") == "1")
    print(f"index.html: {len(merged)}件（オリジナル {n_orig}件）")
    print(f"provided.html: {len(provided) + len(missing)}件"
          f"（musics収録 {len(provided)}件＋未収録 {len(missing)}件）")
    print(f"出力先: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
