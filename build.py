#!/usr/bin/env python3
"""#REALITY歌ってみたYouTubeリスト HTML生成スクリプト。

data/musics.json（機械が集めた事実）に data/annotations.csv（人間の付箋）を
合成して、GitHub Pages 用の docs/index.html を作る。
サムネイルは YouTube の画像URLを直接表示するため、画像の保管は不要。

表示仕様（2026-11-06 ようてん指定）:
- タイトルは X の同名ハッシュタグへのリンク
- 楽曲提供は独立ページを持たず、一覧の絞り込み（バッジ＋フィルタ）で扱う
- credit と note はカード内に「credit、改行、note」の順で表示する
- REALITY / X / YouTube へのリンクはアイコンで表示する
- レイアウトは PC 3ペイン／スマホ 1ペイン、サムネイルは大きめ（hqdefault）

使い方: python3 build.py [--out docs]
確認:  python3 build.py --out /tmp/utattemita-preview
"""
import argparse
import csv
import html
import json
from pathlib import Path
from urllib.parse import quote

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"

TAG = "REALITY歌ってみたYouTubeリスト"
HASHTAG_URL = "https://x.com/hashtag/" + quote(TAG)


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
        # 楽曲提供で musics に未収録の曲（追跡対象外アーティストへの提供）は
        # 一覧には出さないが、件数の辻褄が合うよう呼び出し側で数える。
        merged.append(item)
    return merged


def esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


def br(s: str) -> str:
    return esc(s).replace("\n", "<br>")


def thumb(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{esc(video_id)}/hqdefault.jpg"


# --- アイコン（インラインSVG） -------------------------------------------
# アイコン本体はページ冒頭に <symbol> として1回だけ置き（SPRITE）、
# カード側は <use> で参照する。3,500件超のカードに SVG を複製すると
# ページが肥大化するため。
SPRITE = (
    '<svg width="0" height="0" style="position:absolute" aria-hidden="true">'
    '<defs><linearGradient id="rg" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#2EC5F6"/><stop offset=".45" stop-color="#7B61FF"/>'
    '<stop offset="1" stop-color="#F542A7"/>'
    "</linearGradient></defs>"
    '<symbol id="i-yt" viewBox="0 0 24 24">'
    '<rect x="1.5" y="5" width="21" height="14" rx="4" fill="#FF0033"/>'
    '<path d="M10 9.3v5.4L14.8 12z" fill="#fff"/></symbol>'
    '<symbol id="i-x" viewBox="0 0 24 24"><path fill="#fff" d="'
    "M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68"
    "l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"
    '"/></symbol>'
    '<symbol id="i-re" viewBox="0 0 24 24">'
    '<rect x="5.4" y="5.4" width="13.2" height="13.2" rx="4" '
    'transform="rotate(45 12 12)" fill="none" stroke="url(#rg)" '
    'stroke-width="3.1"/></symbol>'
    "</svg>"
)
ICON_YOUTUBE = '<svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-yt"/></svg>'
ICON_X = '<svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-x"/></svg>'
ICON_REALITY = '<svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-re"/></svg>'


def icon_link(url: str, icon: str, label: str) -> str:
    return (f'<a class="ic" href="{esc(url)}" target="_blank" rel="noopener" '
            f'title="{esc(label)}" aria-label="{esc(label)}">{icon}</a>')


def card_html(item: dict) -> str:
    vid = esc(item.get("video_id", ""))
    watch = f"https://www.youtube.com/watch?v={vid}"
    badges = []
    if item.get("original") == "1":
        badges.append('<span class="badge orig">オリジナル</span>')
    if item.get("provided") == "1":
        badges.append('<span class="badge prov">楽曲提供</span>')
    icons = []
    if item.get("reality_url"):
        icons.append(icon_link(item["reality_url"], ICON_REALITY, "REALITYプロフィール"))
    if item.get("twitter_url"):
        icons.append(icon_link(item["twitter_url"], ICON_X, "Xプロフィール"))
    if item.get("youtube_url"):
        icons.append(icon_link(item["youtube_url"], ICON_YOUTUBE, "YouTubeチャンネル"))
    # credit + 改行 + note（あるものだけ）
    parts = []
    if item.get("credit"):
        parts.append(br(unescape_note(item["credit"])))
    if item.get("note"):
        parts.append(br(item["note"]))
    memo = f'<div class="memo">{"<br>".join(parts)}</div>' if parts else ""
    search_text = " ".join([
        item.get("title", ""), item.get("name", ""),
        unescape_note(item.get("credit", "")), item.get("note", ""),
    ]).lower()
    return (
        f'<article class="card" data-text="{esc(search_text)}" '
        f'data-o="{1 if item.get("original") == "1" else 0}" '
        f'data-p="{1 if item.get("provided") == "1" else 0}">'
        f'<a class="thumb" href="{watch}" target="_blank" rel="noopener">'
        f'<img loading="lazy" src="{thumb(vid)}" alt=""></a>'
        f'<div class="body">'
        f'<div class="title"><a href="{watch}" target="_blank" rel="noopener">'
        f'{esc(item.get("title", ""))}</a></div>'
        + (f'<div class="badges">{" ".join(badges)}</div>' if badges else "")
        + f'<div class="meta"><span class="name">{esc(item.get("name", ""))}</span>'
        f'<span class="date">{esc(item.get("publish_date", ""))}</span>'
        f'<span class="icons">{"".join(icons)}</span></div>'
        f"{memo}</div></article>"
    )


PAGE_CSS = """
:root{--bg:#0d0d12;--card:#17171f;--line:#26262f;--text:#f2f2f5;--muted:#9a9aad;
--accent:#b18cff;--orig:#34d399;--prov:#60a5fa}
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,"Hiragino Kaku Gothic ProN",sans-serif;
background:var(--bg);color:var(--text);margin:0}
header{position:sticky;top:0;z-index:10;background:rgba(13,13,18,.88);
backdrop-filter:blur(10px);border-bottom:1px solid var(--line);padding:14px 20px 10px}
h1{font-size:21px;margin:0 0 10px;letter-spacing:.02em}
h1 a{color:var(--text);text-decoration:none}
h1 a:hover{color:var(--accent)}
.controls{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.controls input[type=search]{flex:1;min-width:180px;padding:9px 12px;font-size:15px;
background:#1e1e28;border:1px solid var(--line);border-radius:999px;color:var(--text)}
.controls button{padding:8px 14px;font-size:13px;border-radius:999px;cursor:pointer;
background:transparent;border:1px solid var(--line);color:var(--muted)}
.controls button.on{background:var(--accent);border-color:var(--accent);color:#17102b;font-weight:700}
#count{color:var(--muted);font-size:13px;margin-left:4px}
main{max-width:1280px;margin:0 auto;padding:20px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}
@media(max-width:1020px){.grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:640px){.grid{grid-template-columns:1fr}main{padding:14px}}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
overflow:hidden;display:flex;flex-direction:column;
transition:transform .15s,border-color .15s,box-shadow .15s}
.card:hover{transform:translateY(-2px);border-color:var(--accent);
box-shadow:0 8px 24px rgba(0,0,0,.45)}
.card .thumb{display:block;aspect-ratio:16/9;background:#000}
.card .thumb img{width:100%;height:100%;object-fit:cover;display:block}
.card .body{padding:12px 14px 14px;display:flex;flex-direction:column;gap:8px;flex:1}
.card .title{font-size:15px;line-height:1.45;font-weight:600}
.card .title a{color:var(--text);text-decoration:none;
display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card .title a:hover{color:var(--accent)}
.badges{display:flex;gap:6px}
.badge{font-size:11px;border-radius:999px;padding:2px 8px;font-weight:700;white-space:nowrap}
.badge.orig{background:rgba(52,211,153,.15);color:var(--orig)}
.badge.prov{background:rgba(96,165,250,.15);color:var(--prov)}
.meta{display:flex;align-items:center;gap:10px;font-size:13px;color:var(--muted);
margin-top:auto}
.meta .name{color:#d8d8e2;font-weight:600;overflow:hidden;text-overflow:ellipsis;
white-space:nowrap}
.meta .date{white-space:nowrap}
.meta .icons{margin-left:auto;display:flex;gap:8px}
.ic{display:inline-flex;opacity:.85;transition:opacity .15s,transform .15s}
.ic:hover{opacity:1;transform:scale(1.12)}
.ic svg{width:20px;height:20px;display:block}
.memo{font-size:12.5px;line-height:1.55;color:#c9c9d4;background:rgba(255,255,255,.04);
border-left:3px solid var(--accent);border-radius:6px;padding:8px 10px}
footer{max-width:1280px;margin:0 auto;padding:8px 20px 28px;color:var(--muted);font-size:12.5px}
footer a{color:var(--muted)}
"""

PAGE_JS = """
const q=document.getElementById('q'), cards=[...document.querySelectorAll('.card')];
let f='all';
function apply(){
  const t=q.value.trim().toLowerCase();
  let n=0;
  for(const c of cards){
    const okText=!t||c.dataset.text.includes(t);
    const okF=f==='all'||(f==='orig'&&c.dataset.o==='1')||(f==='prov'&&c.dataset.p==='1');
    c.style.display=(okText&&okF)?'':'none'; if(okText&&okF)n++;
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


def render_index(merged: list[dict]) -> str:
    cards = "\n".join(card_html(m) for m in merged)
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>#{esc(TAG)}</title><style>{PAGE_CSS}</style></head>
<body>
{SPRITE}
<header>
<h1><a href="{esc(HASHTAG_URL)}" target="_blank" rel="noopener">#{esc(TAG)}</a></h1>
<div class="controls"><input id="q" type="search" placeholder="タイトル・名前・クレジット・メモで検索">
<button data-f="all" class="on">すべて</button>
<button data-f="orig">オリジナル</button>
<button data-f="prov">楽曲提供</button>
<span id="count"></span></div>
</header>
<main><div class="grid">
{cards}
</div></main>
<footer>REALITYで歌ってみた活動を継続している方の新曲を追うリストです。
お問い合わせは <a href="https://x.com/youten_redo" target="_blank" rel="noopener">X @youten_redo</a> へ。</footer>
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

    (out / "index.html").write_text(render_index(merged), encoding="utf-8")

    n_orig = sum(1 for m in merged if m.get("original") == "1")
    n_prov = sum(1 for m in merged if m.get("provided") == "1")
    print(f"index.html: {len(merged)}件（オリジナル {n_orig}件／楽曲提供 {n_prov}件）")
    print(f"出力先: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
