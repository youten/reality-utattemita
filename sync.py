#!/usr/bin/env python3
"""台帳同期スクリプト（annotations.csv を「一覧の台帳」とする方式の中核）。

役割:
  1. data/review_*.csv の decision を取り込む
     - include: 候補の控え（data/candidates.json）から musics.json へ入れ、
       台帳（data/annotations.csv）へ昇格させる
     - exclude: data/excluded.json へ転記する（今後の収集で再掲しない）
  2. 台帳から削除された行を「除外」として取り込む
     （前回同期時の台帳との差分。musics.json からも取り除く）
  3. musics.json にあって台帳に無い曲を台帳へ追加し、新しい順に並べ直す

API は使わない（ファイル操作のみ）。日次ワークフローの収集前後と、
手動の同期アクション（sync.yml）から呼ばれる。何度実行しても同じ結果になる。

使い方: python3 sync.py [--data data]
"""
import argparse
import csv
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent

ANN_FIELDS = ["video_id", "publish_date", "artist", "title",
              "original", "provided", "credit", "note"]


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, obj, **kw) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, **kw) + "\n",
                    encoding="utf-8")


def read_annotations(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return [{k: (r.get(k) or "") for k in ANN_FIELDS}
                for r in csv.DictReader(f) if r.get("video_id")]


def write_annotations(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ANN_FIELDS)
        w.writeheader()
        w.writerows(rows)


def read_review_decisions(data: Path) -> tuple[dict, set]:
    """全 review ファイルから include / exclude の判断を集める。

    戻り値: (include の video_id 集合, exclude の video_id 集合)
    同じ動画に両方ある場合は exclude を優先する（外す意思を安全側に取る）。
    """
    includes, excludes = {}, set()
    for path in sorted(data.glob("review_*.csv")):
        with open(path, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                vid = (r.get("video_id") or "").strip()
                d = (r.get("decision") or "").strip().lower()
                if not vid:
                    continue
                if d == "include":
                    includes[vid] = r
                elif d == "exclude":
                    excludes.add(vid)
    for vid in excludes:
        includes.pop(vid, None)
    return set(includes), excludes


def blank_row(record: dict) -> dict:
    """musics / candidates のレコードから台帳の空行を作る。"""
    return {"video_id": record.get("video_id", ""),
            "publish_date": record.get("publish_date", ""),
            "artist": record.get("name", ""),
            "title": record.get("title", ""),
            "original": "", "provided": "", "credit": "", "note": ""}


def sort_rows(rows: list[dict]) -> list[dict]:
    """新しい順（publish_date 降順）。同日は元の並びを保つ（安定ソート）。"""
    return sorted(rows, key=lambda r: r.get("publish_date") or "", reverse=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(BASE / "data"))
    args = ap.parse_args()
    data = Path(args.data)

    ann_path = data / "annotations.csv"
    musics_path = data / "musics.json"
    cand_path = data / "candidates.json"
    excl_path = data / "excluded.json"
    state_path = data / "sync_state.json"

    rows = read_annotations(ann_path)
    ann_ids = [r["video_id"] for r in rows]
    ann_set = set(ann_ids)
    musics = load_json(musics_path, [])
    by_id = {m["video_id"]: m for m in musics if m.get("video_id")}
    candidates = load_json(cand_path, {})
    excluded = set(load_json(excl_path, []))
    state = load_json(state_path, None)

    includes, review_excludes = read_review_decisions(data)
    n_promoted = n_excluded = n_deleted = n_added = 0
    musics_dirty = cand_dirty = excl_dirty = False

    # 1) exclude の転記（台帳・musics に居るものも外す）
    for vid in sorted(review_excludes):
        if vid not in excluded:
            excluded.add(vid)
            excl_dirty = True
            n_excluded += 1
        if vid in candidates:
            del candidates[vid]
            cand_dirty = True
        if vid in by_id:
            del by_id[vid]
            musics_dirty = True
        if vid in ann_set:
            rows = [r for r in rows if r["video_id"] != vid]
            ann_set.discard(vid)

    # 2) 台帳から削除された行を除外として取り込む
    if state is not None:
        prev_ids = set(state.get("annotation_ids", []))
        for vid in sorted(prev_ids - ann_set):
            if vid in excluded:
                continue
            excluded.add(vid)
            excl_dirty = True
            n_deleted += 1
            if vid in by_id:
                del by_id[vid]
                musics_dirty = True
            if vid in candidates:
                del candidates[vid]
                cand_dirty = True

    # 3) include の昇格（控え → musics ＋ 台帳）
    for vid in sorted(includes):
        if vid in ann_set:
            if vid in candidates:
                del candidates[vid]
                cand_dirty = True
            continue
        record = candidates.pop(vid, None)
        if record is None:
            record = by_id.get(vid)
        if record is None:
            print(f"  昇格できません（曲情報の控えが無い）: {vid}")
            continue
        cand_dirty = True
        if vid not in by_id:
            by_id[vid] = record
            musics_dirty = True
        rows.append(blank_row(record))
        ann_set.add(vid)
        n_promoted += 1

    # 4) musics にあって台帳に無い曲を台帳へ追加
    for m in by_id.values():
        vid = m["video_id"]
        if vid not in ann_set and vid not in excluded:
            rows.append(blank_row(m))
            ann_set.add(vid)
            n_added += 1

    # 5) 昇格・追加済みの候補控えを掃除
    for vid in list(candidates):
        if vid in ann_set or vid in excluded:
            del candidates[vid]
            cand_dirty = True

    # 並べ直し（ID列が変わる場合だけ台帳を書き直す。人間の記入は保持される）
    rows = sort_rows(rows)
    if [r["video_id"] for r in rows] != ann_ids or n_promoted or n_added \
            or review_excludes or n_deleted:
        write_annotations(ann_path, rows)

    if musics_dirty:
        merged = sorted(by_id.values(),
                        key=lambda m: (m.get("publish_date") or "", m.get("no", 0)),
                        reverse=True)
        for i, m in enumerate(merged, 1):
            m["no"] = i
        save_json(musics_path, merged, indent=1)
    if cand_dirty:
        save_json(cand_path, candidates, indent=1, sort_keys=True)
    if excl_dirty:
        save_json(excl_path, sorted(excluded))
    new_state = {"annotation_ids": [r["video_id"] for r in rows]}
    if state != new_state:
        save_json(state_path, new_state)

    print(f"昇格: {n_promoted}件 / 除外の転記: {n_excluded}件 / "
          f"台帳削除の取り込み: {n_deleted}件 / 台帳への追加: {n_added}件")
    print(f"台帳: {len(rows)}件（musics {len(by_id)}件、候補 {len(candidates)}件、"
          f"除外 {len(excluded)}件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
