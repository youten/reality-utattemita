#!/usr/bin/env python3
"""歌ってみた判定ルール v1.3（共有モジュール）。

classification_trial.py の検証済みルール（タイトルのみ）を、
collect.py（実収集）でも同じ判定で使えるよう切り出したもの。
タイトルだけの判定結果は trial と同一になること（検証スクリプトで確認）。

判定は3値 + 保留:
  - "adopt"   : 自動採用
  - "exclude" : 自動除外
  - "review"  : 要確認（人間が見る）
  - "hold"    : プレミア公開前・配信中など。今回は採用せず、次回以降に拾い直す

注意（2026-11-06 時点）:
  - Official MV / Music Video の採用語入りは、pomme さんのチャンネルでの
    2人目検証（2026-11-06）で裏付け済み: MV 系4件は全て採用済み
    オリジナル曲で全件自動採用、麻弓ここね側でも MV 系の誤採用なし。
  - pomme 検証で判明した注意点: 未採用なのに自動採用された9件は全て
    本物の歌ってみたで、うち8件は「採用済み曲の再アップロード／
    #shorts 版」だった。タイトルだけでは重複と新規を区別できないため、
    collect.py 側に「同一アーティスト・同一タイトルの既登録曲がある
    新規動画は重複候補として要確認に回す」を実装済み（2026-11-06、
    ようてんの承認による）。照合ロジックは collect.py の title_keys /
    find_duplicate を参照。pomme の9件は全件検出、自己監査の衝突は
    72件（2.0%）で大半が実際に同一曲の別バージョン。
  - 「オリジナル」はタイトルだけでは判定できないため、original_candidate
    フラグを立てて要確認リストに併記する（DESIGN.md の方針どおり）。

使い方:
    from classify import classify_video
    classify_video({"title": "...", "duration_seconds": 210,
                    "category_id": "10", "tags": ["歌ってみた"],
                    "live_broadcast_content": "none"})
"""
import re

# 強い採用シグナル（タイトルに含まれる語）— trial v1.3 と同一
ADOPT = [
    r"歌ってみた", r"唄ってみた", r"うたってみ", r"うたみた", r"歌みた",
    r"cover", r"covered by", r"カバー", r"歌唱", r"歌って", r"歌いながら",
    r"が歌う",
    r"【original】", r"【オリジナル】", r"original song",
    r"オリジナル曲", r"オリジナル楽曲",
    r"Official MV", r"Music Video", r"【MV】",
]

# 強い除外シグナル（採用シグナルが無い場合のみ適用）— trial v1.3 と同一
EXCLUDE = [
    r"#?shorts", r"雑談", r"歌枠", r"ゲーム", r"実況", r"参加型", r"初見",
    r"🔴", r"メンバーシップ", r"FANBOX",
    r"歌配信", r"記念配信", r"ガチャ配信", r"配信", r"ライブ", r"縦型",
]

# 「オリジナル曲の候補」として要確認リストに併記する目安。
# MV系の表記があり、カバー系の語が無いもの。判定そのものには使わない。
ORIGINAL_CANDIDATE = [
    r"Official MV", r"Music Video", r"【MV】",
    r"オリジナル曲", r"オリジナル楽曲", r"original song",
]
COVER_WORDS = [
    r"cover", r"カバー", r"歌ってみた", r"唄ってみた", r"うたってみ",
    r"歌みた", r"歌唱",
]

# 配信アーカイブ・ゲーム実況の疑いが強い長さの目安（秒）。
# trial の注意点 6：アーカイブは長い。採用語がある場合は要確認に留める。
LONG_VIDEO_SECONDS = 30 * 60

# YouTube カテゴリ ID: 20 = Gaming
GAMING_CATEGORY_ID = "20"


def _clean(title: str) -> str:
    """'#reality配信者' は内容が配信である意味ではないので判定前に除去する。"""
    return re.sub(r"#?reality配信者", "", title or "", flags=re.I)


def is_original_candidate(title: str) -> bool:
    """MV系表記がありカバー語が無い場合に True（要確認リストへの併記用）。"""
    t = _clean(title)
    if not any(re.search(p, t, re.I) for p in ORIGINAL_CANDIDATE):
        return False
    if any(re.search(p, t, re.I) for p in COVER_WORDS):
        return False
    return True


def classify_title(title: str) -> dict:
    """タイトルのみで判定する（trial v1.3 と同一のロジック）。

    戻り値: {"decision": "adopt"|"exclude"|"review",
             "reason": <日本語の理由>,
             "original_candidate": <bool>}
    """
    t = _clean(title)
    adopt = any(re.search(p, t, re.I) for p in ADOPT)
    excl = any(re.search(p, t, re.I) for p in EXCLUDE)
    original_candidate = is_original_candidate(title)
    # 「歌みたが聞きたい?」のような質問系は採用シグナルがあっても要確認へ
    if adopt and re.search(r"聞きたい", t):
        return {"decision": "review", "reason": "採用語あり＋質問系（聞きたい）",
                "original_candidate": original_candidate}
    if adopt:
        return {"decision": "adopt", "reason": "採用語あり",
                "original_candidate": original_candidate}
    if excl:
        # メタバースライブの切り抜き（歌唱クリップ）は要確認へ
        if re.search(r"切り抜き", t) and re.search(r"ライブ|メタバース", t):
            return {"decision": "review", "reason": "ライブ切り抜きの可能性",
                    "original_candidate": original_candidate}
        if re.search(r"アカペラ", t):
            return {"decision": "review", "reason": "アカペラ（判断が分かれる）",
                    "original_candidate": original_candidate}
        return {"decision": "exclude", "reason": "除外語あり・採用語なし",
                "original_candidate": original_candidate}
    # 踊ってみた単独は判断が分かれるため要確認へ
    return {"decision": "review", "reason": "採用語・除外語なし",
            "original_candidate": original_candidate}


def classify_video(meta: dict) -> dict:
    """タイトル + Videos.list の補足情報で判定する。

    meta のキー（無ければタイトルのみとみなす）:
      title, duration_seconds, category_id, tags (list),
      live_broadcast_content ("none"|"upcoming"|"live"),
      privacy_status ("public"|"private"|"unlisted")
    """
    title = meta.get("title", "")
    result = classify_title(title)
    if result["decision"] == "adopt":
        # タイトル採用は維持する。ただし長時間のものは配信アーカイブの
        # 可能性があるため要確認へ（歌唱ライブ等があり得るので除外はしない）
        duration = meta.get("duration_seconds")
        if duration and duration > LONG_VIDEO_SECONDS:
            result = {"decision": "review",
                      "reason": "採用語あり＋長時間（配信アーカイブの可能性）",
                      "original_candidate": result["original_candidate"]}
        return result

    # タイトルだけでは要確認／除外だったものに、補足情報を当てる
    tags = [str(t).lower() for t in (meta.get("tags") or [])]
    if any("歌ってみた" in t or "うたってみた" in t for t in tags):
        if result["decision"] == "review":
            return {"decision": "adopt", "reason": "タグに #歌ってみた",
                    "original_candidate": result["original_candidate"]}

    duration = meta.get("duration_seconds")
    if duration and duration > LONG_VIDEO_SECONDS and result["decision"] == "review":
        return {"decision": "exclude", "reason": "長時間（配信アーカイブの可能性）",
                "original_candidate": result["original_candidate"]}

    if str(meta.get("category_id") or "") == GAMING_CATEGORY_ID \
            and result["decision"] == "review":
        return {"decision": "exclude", "reason": "カテゴリが Gaming",
                "original_candidate": result["original_candidate"]}

    return result


def classify(title: str) -> str:
    """後方互換：trial スクリプト用の簡易版。'adopt' / 'exclude' / 'review' を返す。"""
    return classify_title(title)["decision"]
