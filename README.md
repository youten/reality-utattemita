# #REALITY歌ってみたYouTubeリスト

REALITYユーザーの歌ってみた活動を追う公開リスト。
スプレッドシート（GAS＋手動運用）から GitHub へ移行したもの。
毎日の収集は GitHub Actions で自動実行し、結果を GitHub Pages で公開する。

- 一覧：`docs/index.html`（台帳に載っている全曲・新しい順、検索・絞り込み付き）
- 楽曲提供：独立ページは持たず、一覧の「楽曲提供」フィルタとバッジで表示（台帳の provided=1）

## ファイル構成と所有権（「台帳」方式）

人間の判断は「台帳」と「要確認の decision 欄」の2か所に集約する。
機械が集めた事実は裏側に置き、同期スクリプト（sync.py）が両者を繋ぐ。

| ファイル | 所有者 | 内容 |
|---|---|---|
| `data/artists.csv` | 人間 | 収集先の登録簿（channel ID / playlist ID、187名・213件） |
| `data/annotations.csv` | 人間 | **一覧の台帳**。載っている曲だけがページに出る。全曲・新しい順。`video_id, publish_date, artist, title, original, provided, credit, note`。original / provided / credit / note はここに書き込む。並び替えと行の追加・削除の反映は sync.py が行う |
| `data/review_YYYY-MM-DD.csv` | 機械→人間 | その日の要確認の一覧（判断が要るものだけ。保留＝プレミア等は実行ログにのみ出る）。末尾の `decision` 欄に人間が `include` / `exclude` を書き込む。**消さない運用**（判断の記録になる） |
| `data/musics.json` | 機械 | APIで集めた事実のみ。video_id をキーに管理（重複なし） |
| `data/candidates.json` | 機械 | 要確認に出た曲の情報の控え（昇格時に API を叩き直さないため） |
| `data/excluded.json` | 機械 | 恒久除外の video_id 一覧。review の exclude と台帳からの削除を sync.py が転記する |
| `data/sync_state.json` | 機械 | 前回同期時点の台帳の ID 一覧（削除検出用） |
| `docs/*.html` | 機械 | 生成物 |
| `collect.py` / `sync.py` / `build.py` / `classify.py` | — | 収集・台帳同期・HTML生成・判定ルール（v1.3） |

## 人間の作業

- **フラグ・メモの記入**：GitHubの画面で `data/annotations.csv`（台帳）を直接編集する。
  1行＝1動画、新しい順に並んでいる。`original` / `provided` に 1 を入れる、
  `credit` / `note` を書く。改行を入れたい箇所は文字どおり `\n` と書く。
- **一覧から外す**：台帳からその行を削除する。次回の同期で「除外」として
  記録され、日次収集で復活することはない。
- **要確認の処理**：収集後にできる `data/review_YYYY-MM-DD.csv` を開き、
  各行の `decision` 欄に `include`（載せる）か `exclude`（載せない）を書く。
  空白のままなら保留（翌日以降の review には二重に出てこない）。
  書いた判断は、次回の日次収集のはじめに自動で取り込まれる
  （include は台帳へ昇格、exclude は恒久除外に転記）。
  急ぐ場合は Actions の **sync-ledger** を手動実行すれば、その場で反映される。
  同一アーティストで同一タイトルの登録済み曲がある新規動画は
  「重複候補」として要確認に入る（再アップロードや Shorts 版の
  二重登録防止）。本当に別物なら `include` と書く。
- **新規アーティスト追加**：`data/artists.csv` に行を追加し、
  Actions を手動実行（backfill: true）する。

## セットアップ（初回のみ）

1. このリポジトリを Public で作成し、Pages を `docs/` から有効化する。
2. Google Cloud で YouTube Data API v3 のAPIキーを発行し、
   リポジトリの Secrets に `YOUTUBE_API_KEY` として登録する。
3. Actions の日次実行（毎日22:00 JST）が自動で回る。
   手動実行も可能（`days` / `backfill` / `only` を指定できる）。

## ローカルでの確認（APIキー不要）

```sh
python3 collect.py --dry-run   # 対象件数と quota 見積りだけ表示
python3 sync.py                 # 台帳同期（昇格・除外の取り込み・整備）
python3 build.py                # docs/ を生成
python3 classification_trial.py # 判定ルール v1.3 の検証を再実行
```

## 判定ルール v1.3（概要）

タイトルで自動採用／自動除外／要確認の3値に振り分ける。
採用済み 3,534件の93.3%を自動採用、誤除外は3件（0.1%）。
詳細は `CLASSIFICATION_TRIAL.md` を参照。
「オリジナル」はタイトルだけでは判定できないため、要確認リストに
original候補として併記し、人間が台帳で確定する。
