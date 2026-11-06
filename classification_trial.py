#!/usr/bin/env python3
"""歌ってみた判定ルール v1.3 の試作・検証スクリプト（タイトルのみ）。

検証データ（APIキー不要）:
  - 正例: data/musics.json（採用済み 3,559件）
  - 負例の実例: data_raw/macro.json（麻弓ここねさんのチャンネル全216本。
    ただし「不採用＝歌ってみたでない」とは限らない点に注意。
    2023〜2024年の3件は当時の運用では拾われなかった可能性がある）

判定ルール本体は classify.py に切り出してあり、collect.py と共有している。
このスクリプトは classify.py のタイトル判定が trial v1.3 と同一の結果を
返すことを確認するためのもの。

使い方: python3 classification_trial.py
"""
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify import classify  # noqa: E402

BASE = "/home/hatch/workspace/reality-utattemita"


def report(items, label):
    c = collections.Counter(classify(x["title"]) for x in items)
    n = len(items)
    print(f"{label}: n={n} adopt={c['adopt']} ({c['adopt']/n:.1%}) "
          f"exclude={c['exclude']} ({c['exclude']/n:.1%}) "
          f"review={c['review']} ({c['review']/n:.1%})")
    return c


def video_id(url):
    m = re.search(r"v=([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else None


def main():
    musics = json.load(open(f"{BASE}/data/musics.json", encoding="utf-8"))
    macro = json.load(open(f"{BASE}/data_raw/macro.json", encoding="utf-8"))
    macro_items = [
        {"video_id": video_id(r[7]), "title": r[6], "date": r[4]}
        for r in macro["values"][1:]
        if len(r) >= 8 and video_id(r[7])
    ]
    adopted_ids = {m["video_id"] for m in musics}
    neg = [x for x in macro_items if x["video_id"] not in adopted_ids]
    pos = [x for x in macro_items if x["video_id"] in adopted_ids]

    report(musics, "musics（採用済み・正例）")
    print("  誤って除外されるもの:")
    for m in musics:
        if classify(m["title"]) == "exclude":
            print("   ", repr(m["title"]))
    report(neg, "macro不採用（負例の実例）")
    report(pos, "macro採用済み")
    print("  macro不採用なのに自動採用されるもの:")
    for x in neg:
        if classify(x["title"]) == "adopt":
            print("   ", x["date"], repr(x["title"]))
    recent_neg = [x for x in neg if x["date"] >= "2025/01/01"]
    report(recent_neg, "macro不採用（2025年以降のみ）")
    oct26 = [x for x in macro_items if x["date"] >= "2026/10/01"]
    report(oct26, "macro 2026年10月分（1か月分の目安）")


if __name__ == "__main__":
    main()
