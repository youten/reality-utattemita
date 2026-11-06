# #REALITY歌ってみたYouTubeリスト Git化 設計メモ

2026-11-06 決定・合意済みの方式。現行スプレッドシート（GAS＋手動運用）からの移行先の設計。

## 基本方針：「付箋」方式

人間が決める情報と機械が集める情報を**ファイル単位で完全に分ける**。合成はHTML生成時にだけ行う。

- 自動実行（GitHub Actions）のcommitと手動編集が衝突しない
- 収集を何度走らせても、手動情報が消えたり上書きされたりしない
- 手動作業は「新着分に付箋を付ける」ことだけに縮む

## ファイル構成と所有権

| ファイル | 所有者 | 内容 |
|---|---|---|
| `data/artists.csv` | 人間 | 収集先の登録簿。列：id（UC…/PL…）, kind, name, reality_url, twitter_url, youtube_url。現artistsシート相当 |
| `data/annotations.csv` | 人間 | 動画への付箋（下記仕様）。スクリプトは読み取りのみ |
| `data/musics.json` | 機械 | APIで集めた事実のみ（video_id, title, publish_date, 出どころ, 自動判定結果）。人間は触らない |
| `docs/*.html` | 機械 | 生成物。musics一覧、楽曲提供ページ、アーティスト別など |

## annotations.csv 仕様

- 列：`video_id, title_hint, original, provided, credit, note, decision`
  - `video_id`：キー（watch URLの `v=` の値）
  - `title_hint`：人間が目視で見分けるための参照用。スクリプトは無視する
  - `original`：オリジナル曲なら `1`
  - `provided`：楽曲提供の対象なら `1`
  - `credit`：作曲・作詞などのクレジット（楽曲提供ページに表示）
  - `note`：共演者などのメモ（現musicsシートのNote列相当）
  - `decision`：自動判定への上書き。`include`（強制採用）／`exclude`（強制除外）／空（自動判定に任せる）
- 編集は **GitHubの画面で直接CSVを編集**する（Museを介さない運用が希望）
- 編集しやすくするための約束：
  - 1行＝1動画。改行を入れたい箇所は文字どおり `\n` と書く（生成時に改行に戻す）
  - 要確認リスト（review）には、annotations.csvへそのまま貼れる形の行も併記する

## 収集と判定の流れ

1. `artists.csv` の収集先（プレイリスト／チャンネルのアップロード用プレイリスト）から最近14日分を取得（GAS版の方式を踏襲：PlaylistItems.list、UC→UU変換、ページ上限・空ページ打ち切り、quota超過で全体停止）
2. Videos.list で補足情報を取得（50本あたり1ユニット）：プレミア/配信状態（liveBroadcastContent）、長さ、カテゴリ、タグ
3. 自動判定で3値に振り分け：**自動採用／自動除外／要確認**
   - プレイリスト経由は原則採用（非公開・削除・プレミアだけ落とす）
   - チャンネル経由はルール判定（タイトルの歌系キーワード、Shorts除外、長さ、配信アーカイブ等）
   - プレミア（公開前）は除外でなく「保留」にし、公開された時点で拾い直す
   - original候補（MV系表記がありカバー語が無い等）も要確認リストに併記し、チェックで確定できるようにする
   - `annotations.csv` の decision があれば最優先
4. 動画IDで重複排除して `musics.json` を更新 → 付箋を合成してHTML再生成 → 自動commit → GitHub Pages公開
5. 「楽曲提供」ページは annotations の provided=1 から生成。musics に無い動画（追跡対象外アーティストへの提供曲）は video_id から個別に情報を取得して載せる

## 運用手順

- **日次更新**：Actionsのcron（＋手動実行）で自動。要確認が出たら review を見て、必要なら annotations.csv に decision や original を追記
- **新規アーティスト追加**：artists.csv に行を追加し、backfill実行（全期間を収集→判定→要確認で曲だけ残す）。現行のmacroシート運用の置き換え
- **楽曲提供の追加**：annotations.csv に provided=1 と credit を書く（対象が収集済みでなくてもよい）

## 移行データの状態（2026-11-06 時点、~/workspace/reality-utattemita/data/）

- `artists.csv`：213件（プレイリスト122／チャンネル91、187名）
- `musics.json`：3,534件（2026-11-06に重複25件を除去。元シートは3,559件）
  - 元シートの動画ID重複25件は、IDキー管理により移行先では解消済み（最新公開日の行を残す。元データは `data_raw/musics.json` に保管）
  - 楽曲提供にあってmusicsに無かった7件を照合：6件はartists未登録＝意図的（追跡対象外）。1件（山田奈都美「なちゅなちゅ♡しんどろ〜む」）は登録済みアーティストの取り漏れだったため、移行データでは musics に追加済み
- `annotations.csv`：458行（original 161件、provided 39件、note 285件、credit 39件を現行シートから移行）

## 残作業

- [ ] GitHubリポジトリ作成（公開）と Pages 有効化（ようてんの手元作業）
- [ ] Google Cloudで YouTube Data API v3 のAPIキーを発行し、GitHub Secrets に登録（実収集テストの段階で必要）
- [x] 収集スクリプト（collect.py）の実装（2026-11-06。実収集テストはAPIキー待ち。`--dry-run` で確認済み）
- [x] 分類ルールの実装と、musics／macro実績データでの検証（classify.py に切り出し、collect.py と共有。検証結果は CLASSIFICATION_TRIAL.md）
- [ ] 2人目のチャンネル（pomme）での検証（macro2 のGASリストアップ待ち。Official MV／Music Video の採用語が他チャンネルでも安全かを確認）
- [x] HTML生成（build.py）とビューア仕様の決定（2026-11-06。`docs/index.html`＋`docs/provided.html` を生成確認済み）
- [x] Actions workflow（cron＋手動実行、backfill用）（2026-11-06。`.github/workflows/daily.yml`、毎日22:00 JST）
