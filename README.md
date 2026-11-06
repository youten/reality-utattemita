# #REALITY歌ってみたYouTubeリスト

REALITYユーザーの歌ってみた活動を追う公開リスト。
スプレッドシート（GAS＋手動運用）から GitHub へ移行したもの。
毎日の収集は GitHub Actions で自動実行し、結果を GitHub Pages で公開する。

- 一覧：`docs/index.html`（musics 全件・新しい順、検索・絞り込み付き）
- 楽曲提供：独立ページは持たず、一覧の「楽曲提供」フィルタとバッジで表示（annotations.csv の provided=1）

## ファイル構成と所有権（「付箋」方式）

人間が決める情報と機械が集める情報を、ファイル単位で完全に分ける。
合成は HTML 生成の時だけ行う。

| ファイル | 所有者 | 内容 |
|---|---|---|
| `data/artists.csv` | 人間 | 収集先の登録簿（channel ID / playlist ID、187名・213件） |
| `data/annotations.csv` | 人間 | 動画への付箋。`video_id, title_hint, original, provided, credit, note, decision` |
| `data/musics.json` | 機械 | APIで集めた事実のみ。video_id をキーに管理（重複なし） |
| `data/review_YYYY-MM-DD.csv` | 機械 | その日の要確認・保留の一覧。annotations.csv へ貼れる行を併記 |
| `docs/*.html` | 機械 | 生成物 |
| `collect.py` / `build.py` / `classify.py` | — | 収集・HTML生成・判定ルール（v1.3） |

## 人間の作業

- **付箋の編集**：GitHubの画面で `data/annotations.csv` を直接編集する。
  1行＝1動画。改行を入れたい箇所は文字どおり `\n` と書く。
  `decision` に `include` / `exclude` を書くと自動判定を上書きできる。
- **要確認の処理**：収集後にできる `data/review_YYYY-MM-DD.csv` を見て、
  必要なら付箋に `decision` や `original` を追記する。
  同一アーティストで同一タイトルの登録済み曲がある新規動画は
  「重複候補」として要確認に入る（再アップロードや Shorts 版の
  二重登録防止）。本当に別物なら付箋に `decision=include` を書く。
- **新規アーティスト追加**：`data/artists.csv` に行を追加し、
  Actions を手動実行（backfill: true）する。

## セットアップ（初回のみ）

1. このリポジトリを Public で作成し、Pages を `docs/` から有効化する。
2. Google Cloud で YouTube Data API v3 のAPIキーを発行し、
   リポジトリの Secrets に `YOUTUBE_API_KEY` として登録する。
3. Actions の日次実行（毎日22:00 JST）が自動で回る。
   手動実行も可能（`days` / `backfill` を指定できる）。

## ローカルでの確認（APIキー不要）

```sh
python3 collect.py --dry-run   # 対象件数と quota 見積りだけ表示
python3 build.py                # docs/ を生成
python3 classification_trial.py # 判定ルール v1.3 の検証を再実行
```

## 判定ルール v1.3（概要）

タイトルで自動採用／自動除外／要確認の3値に振り分ける。
採用済み 3,534件の93.3%を自動採用、誤除外は3件（0.1%）。
詳細は `CLASSIFICATION_TRIAL.md` を参照。
「オリジナル」はタイトルだけでは判定できないため、要確認リストに
original候補として併記し、人間がチェックで確定する。
