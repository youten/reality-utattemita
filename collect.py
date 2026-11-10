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

ファイルの所有権（DESIGN.md「台帳」方式、2026-11-06 変更）:
  - 人間が触る: data/artists.csv（収集先の登録簿）、
                data/annotations.csv（一覧の台帳。collect は読まない・書かない）
  - 機械が触る: data/musics.json（video_id をキーに更新）、
                data/review_YYYY-MM-DD.csv（要確認。decision 欄つき）、
                data/candidates.json（要確認の曲情報の控え。昇格用）、
                data/excluded.json（恒久除外の video_id 一覧。読むだけ）
  - 恒久除外（excluded.json）と保留中（candidates.json のキー）の動画は
    再掲しない。review の判断取り込みと台帳の整備は sync.py の担当。

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


def load_id_set(path: Path) -> set:
    """JSON のリスト（video_id の配列）を読む。無ければ空集合。"""
    if not path.exists():
        return set()
    return set(json.load(open(path, encoding="utf-8")))


def load_candidates(path: Path) -> dict:
    """要確認の曲情報の控え（video_id -> レコード）を読む。"""
    if not path.exists():
        return {}
    return json.load(open(path, encoding="utf-8"))


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


def name_key(name: str) -> str:
    """アーティスト名の照合キー（表記ゆれを吸収する）。

    改名や表記ゆれ（絵文字・空白・改行・大文字小文字）を同一視するため、
    NFKC 正規化して英数・かな・漢字だけを残す。データ側の名前は変更せず、
    重複照合のときだけ使う。
    """
    s = unicodedata.normalize("NFKC", name or "").lower()
    return re.sub(r"[^0-9a-z぀-ヿ一-鿿]", "", s)


def _index_keys(record: dict) -> list:
    """レコード／アーティスト行を引くための照合キー群。

    名前（正規化）と YouTube チャンネル URL の2系統。改名した人は
    名前が変わってもチャンネル URL が同じため、旧名義の曲も引ける。
    """
    keys = []
    nk = name_key(record.get("name", ""))
    if nk:
        keys.append(("n", nk))
    url = (record.get("youtube_url") or "").strip()
    if url:
        keys.append(("u", url))
    return keys


def build_registered_index(musics: list[dict]) -> dict:
    """照合キー -> [(キー群, レコード)] の重複照合インデックス。"""
    index: dict[tuple, list] = {}
    for m in musics:
        if m.get("video_id") and m.get("title"):
            pair = (title_keys(m["title"]), m)
            for k in _index_keys(m):
                index.setdefault(k, []).append(pair)
    return index


# ショート動画の採用条件（2026-11-07 ようてんの方針）:
# 基本的には長めの尺の歌ってみたを集めるため、ショート動画は
# タイトルに「歌ってみた」系の語があり、かつ同一タイトルの登録済み曲が
# 無い場合だけ自動採用する。同一タイトルの有無は重複候補チェックが
# 担うので、ここでは尺とタイトルの目印だけを見る。
SHORT_SECONDS = 60
SHORT_TITLE_RE = re.compile(r"ショート|#?shorts", re.I)
UTATTEMITA_RE = re.compile(r"歌ってみた|唄ってみた|うたってみた")


def is_short_form(meta: dict, title: str) -> bool:
    """ショート動画かどうか。尺が60秒以下、またはタイトルに目印がある。"""
    d = meta.get("duration_seconds")
    if d and d <= SHORT_SECONDS:
        return True
    return bool(SHORT_TITLE_RE.search(title or ""))


def has_utattemita_word(title: str) -> bool:
    return bool(UTATTEMITA_RE.search(title or ""))


def find_duplicate(index: dict, artist: dict, title: str):
    """同一アーティストの登録済み曲に同一タイトルがあれば、そのレコードを返す。

    artist は artists.csv の行。名前（正規化）とチャンネル URL の
    どちらかで一致した登録曲をまとめて照合相手にする。
    """
    records, seen = [], set()
    for k in _index_keys(artist):
        for pair in index.get(k, []):
            vid = pair[1].get("video_id")
            if vid not in seen:
                seen.add(vid)
                records.append(pair)
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

    excluded_ids = load_id_set(DATA / "excluded.json")
    candidates = load_candidates(DATA / "candidates.json")
    pending_ids = set(candidates)
    musics = load_musics(DATA / "musics.json")
    by_id = {m["video_id"]: m for m in musics if m.get("video_id")}
    registered = build_registered_index(musics)  # 重複候補の照合用

    cutoff = None if args.backfill else (
        datetime.now(timezone.utc) - timedelta(days=args.days))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # register_date（リストへの追加日）は JST 基準にする。実行が遅延して
    # JST 早朝に走った場合に UTC 日付で前日になるのを防ぐ（2026-11-10）。
    # review ファイル名は従来どおり today（UTC）を使う。
    today_jst = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")

    adopted, review_rows, held, skipped = [], [], [], 0
    suppressed, review_entries = 0, []
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

                if meta.get("privacy_status") in ("private", "unlisted"):
                    continue  # 非公開・限定公開は落とす。限定公開はサムネイルが
                    # あるため GAS 版では混入していたが、API では privacyStatus で判別できる
                if vid in excluded_ids:
                    suppressed += 1
                    continue  # 恒久除外（review で exclude 済み／台帳から削除済み）
                if meta.get("live_broadcast_content") in ("upcoming", "live"):
                    held.append((vid, title, artist.get("name", ""), pub_date,
                                 meta["live_broadcast_content"]))
                    continue  # プレミア公開前・配信中は保留。公開後に拾い直す
                orig_flag = ""
                if kind == "playlist":
                    # プレイリスト経由は本人の歌ってみたプレイリスト中心のため原則採用
                    verdict, reason = "adopt", "プレイリスト経由（原則採用）"
                else:
                    r = classify_video(meta)
                    verdict, reason = r["decision"], r["reason"]
                    orig_flag = "1" if r.get("original_candidate") else ""
                # 重複候補チェック：同一アーティストで同一タイトルの登録済み曲が
                # ある新規動画は、再アップロード等の可能性があるため要確認へ。
                # 既に登録済みの同一動画は対象外。
                if verdict == "adopt" and vid not in by_id:
                    dup = find_duplicate(registered, artist, title)
                    if dup is not None:
                        verdict = "review"
                        reason = (f"重複候補（同一タイトルの登録済み: "
                                  f"{dup.get('title', '')} / {dup.get('publish_date', '')}）")
                        if not orig_flag:
                            orig_flag = "1" if is_original_candidate(title) else ""
                # ショート動画は、タイトルに「歌ってみた」系の語が無いものを
                # 自動採用しない（同一タイトルの登録済み曲がある場合は
                # 上の重複候補チェックで要確認になっている）
                if verdict == "adopt" and is_short_form(meta, title) \
                        and not has_utattemita_word(title):
                    verdict = "review"
                    reason = "ショート動画（タイトルに「歌ってみた」なし）"
                    if not orig_flag:
                        orig_flag = "1" if is_original_candidate(title) else ""
                entry = {
                    "video_id": vid, "title": title, "publish_date": pub_date,
                    "name": artist.get("name", ""), "reality_url": artist.get("reality_url", ""),
                    "twitter_url": artist.get("twitter_url", ""),
                    "youtube_url": artist.get("youtube_url", ""),
                    "watch_url": f"https://www.youtube.com/watch?v={vid}",
                    "register_date": today_jst.replace("-", "/"),
                    "source": kind or "channel",
                    "decision": verdict, "decision_reason": reason,
                }
                if verdict == "adopt":
                    adopted.append(entry)
                    # 今回採用した動画も照合対象に加え、同一実行内での
                    # 重複アップロード（2本目以降）を重複候補に回す
                    for k in _index_keys(entry):
                        registered.setdefault(k, []).append(
                            (title_keys(title), entry))
                elif verdict == "review":
                    if vid in pending_ids:
                        pass  # すでに要確認に出ている（再掲しない）
                    else:
                        review_rows.append((vid, title, artist.get("name", ""), pub_date,
                                            kind, reason, orig_flag))
                        review_entries.append(entry)  # 昇格用に曲情報の控えを残す
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
    # decision 欄は人間が include / exclude を書き込むための空欄。
    # include と書かれた行は sync.py が台帳へ昇格させる（曲情報の控えは
    # candidates.json に残すため、昇格時に API は不要）。
    review_path = None
    if review_rows:
        review_path = DATA / f"review_{today}{suffix}.csv"
        with open(review_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["video_id", "title_hint", "artist", "publish_date", "source",
                        "verdict", "reason", "original_candidate", "decision"])
            for vid, title, name, pub, kind, reason, orig in review_rows:
                w.writerow([vid, title, name, pub, kind, "review", reason, orig, ""])
    if review_entries:
        for e in review_entries:
            candidates[e["video_id"]] = e
        with open(DATA / "candidates.json", "w", encoding="utf-8") as f:
            json.dump(candidates, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")

    print(f"採用（新規追加）: {added}件（今回の取得で採用判定 {len(adopted)}件）")
    print(f"要確認: {len(review_rows)}件、保留（プレミア等）: {len(held)}件")
    if suppressed:
        print(f"恒久除外のため読み飛ばし: {suppressed}件")
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
