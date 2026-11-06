# #REALITY歌ってみたYouTubeリスト Git化 設計メモ

2026-11-06 決定・合意済みの方式。現行スプレッドシート（GAS＋手動運用）からの移行先の設計。

## 基本方針：「台帳」方式（2026-11-06 夕方に「付箋」方式から変更）

人間の判断は2か所に集約する：**一覧の台帳（annotations.csv）**と
**要確認の decision 欄**。機械が集めた事実は裏側（musics.json 等）に置き、
同期スクリプト（sync.py）が判断を取り込んで台帳を整える。

変更の理由：付箋方式では annotations.csv のあちこちを編集する作業が
分散して大変だったため。新しい方式では、

- 台帳に載っていること自体が「一覧に載せる」という意思（decision 欄は廃止）
- 要確認への回答は review ファイルの decision 欄に書くだけで、
  昇格・除外の反映は sync.py が自動で行う
- 台帳から行を消す＝一覧から外す、という1つの操作に統一される

## ファイル構成と所有権

| ファイル | 所有者 | 内容 |
|---|---|---|
| `data/artists.csv` | 人間 | 収集先の登録簿。列：id（UC…/PL…）, kind, name, reality_url, twitter_url, youtube_url。現artistsシート相当 |
| `data/annotations.csv` | 人間 | 一覧の台帳（下記仕様）。sync.py が行の追加・並び替え・削除の反映を行う |
| `data/review_YYYY-MM-DD.csv` | 機械→人間 | 要確認の一覧。人間が decision 欄に include / exclude を書く。消さない運用 |
| `data/musics.json` | 機械 | APIで集めた事実のみ（video_id, title, publish_date, 出どころ, 自動判定結果）。人間は触らない |
| `data/candidates.json` | 機械 | 要確認の曲情報の控え（昇格用） |
| `data/excluded.json` | 機械 | 恒久除外の video_id 一覧（sync.py が転記） |
| `data/sync_state.json` | 機械 | 前回同期時点の台帳 ID 一覧（台帳からの削除検出用） |
| `docs/*.html` | 機械 | 生成物。台帳にある曲だけを描く |

## annotations.csv（台帳）仕様

- 列：`video_id, publish_date, artist, title, original, provided, credit, note`
  - `video_id`：キー（watch URLの `v=` の値）
  - `publish_date` / `artist` / `title`：目視確認用（表示の正は musics.json の事実。手で行を追加した場合はこの列が使われる）
  - `original`：オリジナル曲なら `1`
  - `provided`：楽曲提供の対象なら `1`
  - `credit`：作曲・作詞などのクレジット
  - `note`：共演者などのメモ（現musicsシートのNote列相当）
- 並びは公開日の新しい順。sync.py が維持する
- 編集は **GitHubの画面で直接CSVを編集**する（Museを介さない運用が希望）
- 編集しやすくするための約束：
  - 1行＝1動画。改行を入れたい箇所は文字どおり `\n` と書く（生成時に改行に戻す）
  - 行を削除すると、その曲は一覧から外れ、恒久除外として記録される（収集で復活しない）

## review_YYYY-MM-DD.csv 仕様

- 列：`video_id, title_hint, artist, publish_date, source, verdict, reason, original_candidate, decision`
- `decision` は人間が書く欄：`include`（台帳へ昇格）／`exclude`（恒久除外）／空（保留）
- すでにどこかの review に載っている動画は再掲しない。exclude された動画も出てこない
- sync.py が全 review ファイルの decision を読み、include は candidates.json の
  控えから musics.json＋台帳へ昇格、exclude は excluded.json へ転記する

## 収集と判定の流れ

1. `artists.csv` の収集先（プレイリスト／チャンネルのアップロード用プレイリスト）から最近14日分を取得（GAS版の方式を踏襲：PlaylistItems.list、UC→UU変換、ページ上限・空ページ打ち切り、quota超過で全体停止）
2. Videos.list で補足情報を取得（50本あたり1ユニット）：プレミア/配信状態（liveBroadcastContent）、長さ、カテゴリ、タグ
3. 自動判定で3値に振り分け：**自動採用／自動除外／要確認**
   - プレイリスト経由は原則採用（非公開・削除・プレミアだけ落とす）
   - チャンネル経由はルール判定（タイトルの歌系キーワード、Shorts除外、長さ、配信アーカイブ等）
   - プレミア（公開前）は除外でなく「保留」にし、公開された時点で拾い直す
   - original候補（MV系表記がありカバー語が無い等）も要確認リストに併記し、チェックで確定できるようにする
   - 恒久除外（excluded.json）と保留中（candidates.json）の動画は判定対象から外す
4. 動画IDで重複排除して `musics.json` を更新
5. sync.py（日次では収集の前後、手動では sync-ledger アクション）が review の判断を取り込み、台帳を整える → build.py が台帳＋事実からHTML再生成 → 自動commit → GitHub Pages公開
5. 「楽曲提供」は独立ページを持たず、一覧のフィルタとバッジで表示する（2026-11-06 のデザイン変更で provided.html は廃止）。追跡対象外アーティストへの提供曲6件は、他の曲をリストアップしない方針のため artists には入れず、musics.json に静的に保持して表示する（2026-11-06 方針決定。一時 channel 追加して取り込んだが同日中に戻した）

## 運用手順

- **日次更新**：Actionsのcron（＋手動実行）で自動。要確認が出たら review の decision 欄に include / exclude を書く（翌日の収集で自動反映。急ぐ場合は sync-ledger を手動実行）
- **新規アーティスト追加**：artists.csv に行を追加し、backfill実行（全期間を収集→判定→要確認で判断）
- **楽曲提供の追加**：台帳（annotations.csv）の該当行に provided=1 と credit を書く
- **一覧から外す**：台帳から行を削除する

## 移行データの状態（2026-11-06 時点、~/workspace/reality-utattemita/data/）

- `artists.csv`：213件（プレイリスト122／チャンネル91、187名）
- `musics.json`：3,534件（2026-11-06に重複25件を除去。元シートは3,559件）
  - 元シートの動画ID重複25件は、IDキー管理により移行先では解消済み（最新公開日の行を残す。元データは `data_raw/musics.json` に保管）
  - 楽曲提供にあってmusicsに無かった7件を照合：6件はartists未登録＝意図的（追跡対象外）。1件（山田奈都美「なちゅなちゅ♡しんどろ〜む」）は登録済みアーティストの取り漏れだったため、移行データでは musics に追加済み
- `annotations.csv`：当初は付箋458行で移行。2026-11-06 夕方に台帳方式へ作り替え、全3,541曲＋引き継いだ付箋情報（original 161件、provided 39件、note 285件、credit 39件）。旧 decision=exclude の5件は excluded.json へ移行

## 残作業

- [ ] GitHubリポジトリ作成（公開）と Pages 有効化（ようてんの手元作業）
- [ ] Google Cloudで YouTube Data API v3 のAPIキーを発行し、GitHub Secrets に登録（実収集テストの段階で必要）
- [x] 収集スクリプト（collect.py）の実装（2026-11-06。実収集テストはAPIキー待ち。`--dry-run` で確認済み）
- [x] 分類ルールの実装と、musics／macro実績データでの検証（classify.py に切り出し、collect.py と共有。検証結果は CLASSIFICATION_TRIAL.md）
- [ ] 2人目のチャンネル（pomme）での検証（macro2 のGASリストアップ待ち。Official MV／Music Video の採用語が他チャンネルでも安全かを確認）
- [x] HTML生成（build.py）とビューア仕様の決定（2026-11-06。`docs/index.html`＋`docs/provided.html` を生成確認済み）
- [x] Actions workflow（cron＋手動実行、backfill用）（2026-11-06。`.github/workflows/daily.yml`、毎日22:00 JST）
