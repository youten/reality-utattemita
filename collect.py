#!/usr/bin/env python3
"""#REALITY歌ってみたYouTubeリスト 日次収集スクリプト。

GAS版（gas_original.gs）の移植：PlaylistItems.list 中心で最近14日分を集め、
Videos.list で補足情報（長さ・カテゴリ・タグ・配信状態）を取って判定する。
判定ルールは classify.py（v1.3 + メタデータ併用）。
非公開・限定公開（unlisted）の動画は収集対象から外し、プレミア公開前と
配信中は「保留」として今回は入れず、公開された後の収集で拾い直す。
あわせて、同一アーティストで同一タイトル（表記ゆれを吸収した照合）の
登録済み曲がある新規動画は「重複候補」として自動採用せず要確認に回す
（再アップロードや Shorts 版の二重登録防止。pomme 検証 2026-11-06 の結論）。
付箋 decision=include の動画は人間の判断を優先し、重複候補の対象にしない。

ファイルの所有権（DESIGN.md「付箋」方式）:
  - 人間が触る: data/artists.csv / data/annotations.csv（読むだけ。書かない）
  - 機械が触る: data/musics.json（video_id をキーに更新）、
                data/review_YYYY-MM-DD.csv（要確認・保留の一覧）

使い方:
  YOUTUBE_API_KEY=... python3 collect.py [--days 14] [--backfill]
      [--artists data/artists.csv] [--dry-run] [--only <id,...>]
  --days: 収集する過去日数（既定 14。GASの DAYS と同じ）
  --backfill: 日付フィルタなしで全期間を収集（新規アーティスト追加時）
  --dry-run: APIを呼ばず、対象件数と quota 見積りだけ表示する
  --only: 指定した収集先IDだけ処理する（カンマ区切り）

APIキー不要の確認: python3 collect.py --dry-run
"""
import argparse
import csv
import html
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify import classify_video, is_original_candidate  # noqa: E402

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
DAYS = 14
MAX_PAGES_PER_ID = 40        # GAS版と同じ安全上限
MAX_EMPTY_PAGES = 2          # 空ページ連続で打ち切り（GAS版と同じ）
API_BASE = "https://www.googleapis.com/youtube/v3"


class QuotaExceeded(Exception):
    pass


def parse_duration_iso8601(value: str) -> int | None:
    """PT1H2M3S / PT4M5S などを秒に変換する。"""
    if not value:
        return None
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not m:
        return None
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def to_uploads_playlist_id(channel_id: str) -> str | None:
    s = (channel_id or "").strip()
    return "UU" + s[2:] if s.startswith("UC") else None


def resolve_channel_id(api_key: str, handle: str) -> str | None:
    """@ハンドルからチャンネルID（UC...）を引く。GAS版と同じ Channels.list(forHandle)。"""
    data = yt_get(api_key, "channels", {"part": "id", "forHandle": handle})
    items = data.get("items") or []
    return items[0].get("id") if items else None


def yt_get(api_key: str, path: str, params: dict) -> dict:
    """YouTube Data API v3 を1回呼ぶ。quota超過は QuotaExceeded にする。"""
    q = dict(params, key=api_key)
    url = f"{API_BASE}/{path}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(url, headers={"User-Agent": "reality-utattemita-collect/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.load(res)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        if "quotaExceeded" in body or "quota" in body.lower():
            raise QuotaExceeded(body[:500])
        raise


def fetch_playlist_items(api_key: str, playlist_id: str, published_after=None,
                         stop_when_old=False):
    """PlaylistItems.list で動画を集める（GAS版 getVideosFromPlaylistId の移植）。"""
    items, next_token, pages, empty_pages = [], "", 0, 0
    while next_token is not None:
        res = yt_get(api_key, "playlistItems", {
            "part": "snippet,status,contentDetails",
            "maxResults": 50, "playlistId": playlist_id,
            "pageToken": next_token} if next_token else {
            "part": "snippet,status,contentDetails",
            "maxResults": 50, "playlistId": playlist_id})
        batch = res.get("items", [])
        for it in batch:
            raw = (it.get("contentDetails") or {}).get("videoPublishedAt")
            if published_after is not None:
                # videoPublishedAt が無いエントリ（削除分など）があるため、
                # None のまま fromisoformat に渡さない（AttributeError で落ちる）
                try:
                    pub = datetime.fromisoformat(
                        raw.replace("Z", "+00:00")) if raw else None
                except ValueError:
                    pub = None
                if pub is None:
                    continue  # 日付が取れないエントリは読み飛ばす
                    # （チャンネル収集で「古い動画に当たった」と誤認して
                    # 　打ち切ると、その日の新曲を取りこぼすため）
                if pub < published_after:
                    if stop_when_old:
                        return items
                    continue
            items.append(it)
        next_token = res.get("nextPageToken")
        if not batch:
            empty_pages += 1
            if empty_pages >= MAX_EMPTY_PAGES:
                break
        else:
            empty_pages = 0
        pages += 1
        if pages >= MAX_PAGES_PER_ID:
            break
    return items


def fetch_video_details(api_key: str, video_ids: list[str]) -> dict:
    """Videos.list で補足情報を50件ずつ取得する。video_id -> 詳細の dict を返す。"""
    details = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i + 50]
        res = yt_get(api_key, "videos", {
            "part": "snippet,status,contentDetails",
            "id": ",".join(chunk)})
        for v in res.get("items", []):
            sn, st, cd = v.get("snippet", {}), v.get("status", {}), v.get("contentDetails", {})
            details[v["id"]] = {
                "duration_seconds": parse_duration_iso8601(cd.get("duration", "")),
                "category_id": sn.get("categoryId"),
                "tags": sn.get("tags") or [],
                "live_broadcast_content": sn.get("liveBroadcastContent", "none"),
                "privacy_status": st.get("privacyStatus", "public"),
            }
    return details


def load_artists(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if (r.get("id") or "").strip()]


def load_annotations(path: Path) -> dict:
    """video_id -> 付箋行。decision（include/exclude）が最優先される。"""
    out = {}
    if not path.exists():
        return out
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("video_id"):
                out[row["video_id"]] = row
    return out


def load_musics(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.load(open(path, encoding="utf-8"))


_DUP_FILLER = [
    r"covered\s*by", r"cover", r"カバー",
    r"歌ってみた", r"唄ってみた", r"うたってみた", r"歌みた", r"うたみた",
]
def title_keys(title: str) -> dict:
    """重複照合用のキー群を作る（表記ゆれを吸収する）。

    full: 全体を正規化した文字列。tokens: 英数字の連なりと日本語の連なりを
    単位にした語の集合（「ラプンツェル Rapunzel / n-buna」のような
    日本語タイトル＋ローマ字表記の揺れを吸収するため）。
    ハッシュタグ・括弧内・feat. 節・歌ってみた等の定型語・空白と記号を除き、
    NFKC で濁点の分解表記などを統一する。

    注意: 日本語部分だけ／英字部分だけを抜いたキーは「原曲アーティスト名
    だけが残る」事故（YOASOBI の別曲同士が一致する等）を起こすため使わない。
    """
    t = html.unescape(unicodedata.normalize("NFKC", title or "")).lower()
    # ハッシュタグは空白・括弧・区切り文字までで止める（"#タグ】曲名" の
    # ように空白なしで続く場合に、曲名まで巻き込んで消さないため）
    t = re.sub(r"#[^\s【】（）()\[\]、,・/／|｜#]*", "", t)
    # 括弧は中身を残して外すだけにする（【シャンティ 歌ってみた】のように
    # 曲名が括弧内にあるタイトルで、曲名ごと消えてしまう事故を防ぐ）。
    # 空文字ではなく空白に置き換えるのは、"HACHI【Official" のように
    # 括弧の前後が英単語同士の場合に、語がくっついてしまうのを防ぐため。
    t = re.sub(r"[【】\[\]（）()｢｣]", " ", t)
    t = re.sub(r"feat\.?[^/／|｜]*", "", t)
    for w in _DUP_FILLER:
        t = re.sub(w, "", t)
    # 語の切り分けは区切り文字が残っている段階で行う（flatten してから
    # 切り分けると "rapunzel / n-buna" が1語に潰れてしまうため）。
    # 1文字の英数字と "by" はノイズとして落とす。
    tokens = frozenset(
        tok for tok in re.findall(r"[0-9a-z]+|[\u3040-\u30ff\u4e00-\u9fff]+", t)
        if tok != "by" and not (len(tok) == 1 and tok.isascii()))
    full = re.sub(r"[^0-9a-z\u3040-\u30ff\u4e00-\u9fff]+", "", t)
    return {"full": full, "tokens": tokens}


def keys_collide(a: dict, b: dict, distinctive: set | None = None,
                 allow_weak: bool = True) -> bool:
    """2つのタイトルのキー群が重複とみなせるほど一致するか。

    distinctive: そのアーティストの中で「ありふれていない」語の集合。
    弱い照合（文字列の包含・語の包含）は、小さい側に distinctive な語が
    1つ以上ある場合だけ認める（アーティスト名や「Official MV」のような
    定型句だけの一致を重複とみなさないため）。allow_weak=False の場合は
    完全一致のみで判定する。
    """
    x, y = a.get("full", ""), b.get("full", "")
    if x and x == y and len(x) >= 4:
        return True
    if not allow_weak:
        return False
    if len(x) >= 8 and len(y) >= 8 and (x in y or y in x):
        shorter = a if len(x) <= len(y) else b
        if distinctive is None or (shorter.get("tokens") or frozenset()) & distinctive:
            return True
    ta, tb = a.get("tokens") or frozenset(), b.get("tokens") or frozenset()
    if ta and tb and (ta <= tb or tb <= ta):
        smaller = ta if len(ta) <= len(tb) else tb
        total = sum(len(tok) for tok in smaller)
        # 1語だけのキーは「アーティスト名のみ」等の事故があり得るため厳しめに
        if total >= 6 and (len(smaller) >= 2 or total >= 7):
            if distinctive is None or smaller & distinctive:
                return True
    return False


def build_registered_index(musics: list[dict]) -> dict:
    """アーティスト名 -> [(キー群, レコード)] の重複照合インデックス。"""
    index: dict[str, list] = {}
    for m in musics:
        if m.get("video_id") and m.get("title"):
            index.setdefault(m.get("name", ""), []).append((title_keys(m["title"]), m))
    return index


def find_duplicate(index: dict, name: str, title: str):
    """同一アーティストの登録済み曲に同一タイトルがあれば、そのレコードを返す。"""
    records = index.get(name, [])
    if not records:
        return None
    # そのアーティストの登録曲の1割以下にしか現れない語を distinctive とする
    # （シリーズ定型句のような「そこそこ出てくる語」を distinctive と
    # 誤認しないよう、割合は厳しめにする）
    df: dict[str, int] = {}
    for ex_keys, _rec in records:
        for tok in ex_keys.get("tokens") or ():
            df[tok] = df.get(tok, 0) + 1
    threshold = max(2, len(records) // 10)
    distinctive = {tok for tok, c in df.items() if c <= threshold}
    keys = title_keys(title)
    distinctive |= {tok for tok in keys.get("tokens") or ()
                    if df.get(tok, 0) <= threshold}
    # 新規タイトルに distinctive な語が1つも無い場合（曲名が記号や1文字で
    # 消えてしまい「アーティスト名＋Official MV」だけが残る等）は、
    # 弱い照合（包含・語の包含）は誤検知の元になるため完全一致だけを見る
    allow_weak = bool((keys.get("tokens") or frozenset()) & distinctive)
    for ex_keys, record in records:
        if keys_collide(keys, ex_keys, distinctive, allow_weak):
            return record
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=DAYS)
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--artists", default=str(DATA / "artists.csv"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    artists = load_artists(Path(args.artists))
    if args.only:
        only = {x.strip() for x in args.only.split(",") if x.strip()}
        artists = [a for a in artists if a["id"] in only]
    n_playlist = sum(1 for a in artists if a.get("kind") == "playlist")
    n_channel = len(artists) - n_playlist
    # quota見積り：PlaylistItems 1/ページ（最大40ページだが通常は14日分で1〜2ページ）、
    # Videos.list 50本あたり1
    print(f"対象: {len(artists)}件（プレイリスト {n_playlist}件／チャンネル {n_channel}件）")
    if args.dry_run:
        print(f"期間: {'全期間（backfill）' if args.backfill else f'過去{args.days}日'}")
        print("quota見積り: PlaylistItems は対象1件あたり通常1〜2（上限40）、"
              "Videos.list は動画50本あたり1。標準上限10,000/日に対して十分余裕がある見込み。")
        print("dry-run のため API は呼びませんでした。")
        return 0

    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        print("YOUTUBE_API_KEY が設定されていません。", file=sys.stderr)
        return 2

    annotations = load_annotations(DATA / "annotations.csv")
    musics = load_musics(DATA / "musics.json")
    by_id = {m["video_id"]: m for m in musics if m.get("video_id")}
    registered = build_registered_index(musics)  # 重複候補の照合用

    cutoff = None if args.backfill else (
        datetime.now(timezone.utc) - timedelta(days=args.days))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    adopted, review_rows, held, skipped = [], [], [], 0
    quota_stop = False
    try:
        for artist in artists:
            if quota_stop:
                break
            kind = (artist.get("kind") or "").strip()
            if kind == "playlist":
                playlist_id = artist["id"]
                stop_old = False
            else:
                channel_id = artist["id"]
                if channel_id.startswith("@"):
                    # ハンドル登録のチャンネルはIDを引いてから変換する
                    try:
                        channel_id = resolve_channel_id(api_key, channel_id) or ""
                    except QuotaExceeded:
                        print("quota超過を検知したため中断します。", file=sys.stderr)
                        quota_stop = True
                        break
                playlist_id = to_uploads_playlist_id(channel_id)
                stop_old = True
                if playlist_id is None:
                    print(f"  skip: {artist['id']} はUC始まりでないため変換不可", file=sys.stderr)
                    continue
            try:
                items = fetch_playlist_items(api_key, playlist_id, cutoff, stop_old)
            except QuotaExceeded:
                print("quota超過を検知したため中断します。", file=sys.stderr)
                quota_stop = True
                break
            ids = []
            for it in items:
                vid = (it.get("contentDetails") or {}).get("videoId")
                if vid:
                    ids.append(vid)
            details = fetch_video_details(api_key, ids) if ids else {}
            for it in items:
                vid = (it.get("contentDetails") or {}).get("videoId")
                if not vid:
                    continue
                sn = it.get("snippet", {})
                title = sn.get("title", "")
                if title == "Deleted video":
                    continue
                pub_raw = (it.get("contentDetails") or {}).get("videoPublishedAt", "")
                try:
                    pub = datetime.fromisoformat((pub_raw or "").replace("Z", "+00:00"))
                    pub_date = pub.strftime("%Y/%m/%d")
                except ValueError:
                    pub_date = ""
                meta = dict(details.get(vid, {}), title=title)
                ann = annotations.get(vid, {})
                decision = (ann.get("decision") or "").strip().lower()

                if meta.get("privacy_status") in ("private", "unlisted"):
                    continue  # 非公開・限定公開は落とす。限定公開はサムネイルが
                    # あるため GAS 版では混入していたが、API では privacyStatus で判別できる
                if meta.get("live_broadcast_content") in ("upcoming", "live"):
                    held.append((vid, title, artist.get("name", ""), pub_date,
                                 meta["live_broadcast_content"]))
                    continue  # プレミア公開前・配信中は保留。公開後に拾い直す
                orig_flag = ""
                if decision == "include":
                    verdict, reason = "adopt", "付箋 decision=include"
                elif decision == "exclude":
                    verdict, reason = "exclude", "付箋 decision=exclude"
                elif kind == "playlist":
                    # プレイリスト経由は本人の歌ってみたプレイリスト中心のため原則採用
                    verdict, reason = "adopt", "プレイリスト経由（原則採用）"
                else:
                    r = classify_video(meta)
                    verdict, reason = r["decision"], r["reason"]
                    orig_flag = "1" if r.get("original_candidate") else ""
                # 重複候補チェック：同一アーティストで同一タイトルの登録済み曲が
                # ある新規動画は、再アップロード等の可能性があるため要確認へ。
                # 付箋 include（人間の判断）と、既に登録済みの同一動画は対象外。
                if verdict == "adopt" and decision != "include" and vid not in by_id:
                    dup = find_duplicate(registered, artist.get("name", ""), title)
                    if dup is not None:
                        verdict = "review"
                        reason = (f"重複候補（同一タイトルの登録済み: "
                                  f"{dup.get('title', '')} / {dup.get('publish_date', '')}）")
                        if not orig_flag:
                            orig_flag = "1" if is_original_candidate(title) else ""
                entry = {
                    "video_id": vid, "title": title, "publish_date": pub_date,
                    "name": artist.get("name", ""), "reality_url": artist.get("reality_url", ""),
                    "twitter_url": artist.get("twitter_url", ""),
                    "youtube_url": artist.get("youtube_url", ""),
                    "watch_url": f"https://www.youtube.com/watch?v={vid}",
                    "register_date": today.replace("-", "/"),
                    "source": kind or "channel",
                    "decision": verdict, "decision_reason": reason,
                }
                if verdict == "adopt":
                    adopted.append(entry)
                    # 今回採用した動画も照合対象に加え、同一実行内での
                    # 重複アップロード（2本目以降）を重複候補に回す
                    registered.setdefault(entry["name"], []).append(
                        (title_keys(title), entry))
                elif verdict == "review":
                    review_rows.append((vid, title, artist.get("name", ""), pub_date,
                                        kind, reason, orig_flag))
                # exclude は musics に入れない
    except QuotaExceeded:
        quota_stop = True

    # musics.json を video_id キーで更新（既存の付箋由来フィールドは残す）
    added = 0
    for e in adopted:
        old = by_id.get(e["video_id"])
        if old is None:
            by_id[e["video_id"]] = e
            added += 1
        else:
            old.update({k: v for k, v in e.items()
                        if k not in ("note", "original") or not old.get(k)})
    merged = sorted(by_id.values(), key=lambda m: (m.get("publish_date") or "", m.get("no", 0)),
                    reverse=True)
    for i, m in enumerate(merged, 1):
        m["no"] = i
    with open(DATA / "musics.json", "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)
        f.write("\n")

    suffix = "_backfill" if (args.backfill or args.only) else ""
    # 要確認ファイルは「人間の判断が要るもの」だけを書く。保留（プレミア等）は
    # 判断不要で、公開まで毎日のファイルに出続けてノイズになるため、CSV には
    # 混ぜず実行ログにのみ出す（2026-11-06 ようてんの指摘で変更）。
    # 要確認が無い日はファイル自体を作らない。
    review_path = None
    if review_rows:
        review_path = DATA / f"review_{today}{suffix}.csv"
        with open(review_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["video_id", "title_hint", "artist", "publish_date", "source",
                        "verdict", "reason", "original_candidate", "suggested_annotation_row"])
            for vid, title, name, pub, kind, reason, orig in review_rows:
                ann_row = ",".join([vid, title.replace(",", "、"), orig, "", "", "", ""])
                w.writerow([vid, title, name, pub, kind, "review", reason, orig, ann_row])

    print(f"採用（新規追加）: {added}件（今回の取得で採用判定 {len(adopted)}件）")
    print(f"要確認: {len(review_rows)}件、保留（プレミア等）: {len(held)}件")
    for vid, title, name, pub, status in held:
        print(f"  保留: {title}（{name}、{status}）")
    print(f"musics.json: {len(merged)}件に更新")
    if review_path is not None:
        print(f"要確認リスト: {review_path}")
    else:
        print("要確認はありませんでした。")
    if quota_stop:
        print("※ quota超過で中断したため、未処理の対象が残っています。", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
