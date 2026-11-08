# 出走前情報のローカル取り込み（Windows）

この機能は、**利用権限が確認できたデータ**を手元のCSVファイルから取り込むためのものです。外部サイトへの自動アクセス、スクレイピング、API接続、予測、購入やDiscord通知は行いません。

## 1. 空のCSVひな形を作る

Keirinリポジトリを最新版に更新し、ルートでコマンドプロンプトを開きます。

```cmd
py -3 tools\intake_pre_race.py --template local_data\entry_template.csv
```

`local_data/entry_template.csv` に**ヘッダーだけ**が作られます。架空の選手データを自動補完することはありません。

## 2. 実際の出走前情報を入力する

取得・利用・保存を許可されたソースを使って、**締切前**に1車1行として以下を入力してください。1レースにつき7～9行必要です。

`race_id,race_date,car_no,observed_at_jst,deadline_at_jst,start_at_jst,source_url,rights_status,line_id,rating,recent_win_rate,recent_top2_rate,recent_top3_rate,line_position,age`

- `race_id`：既存の `exports/results_3rentan.csv` との結合に用いる共通ID。形式を揃えます。
- `race_date`：`YYYY-MM-DD`。
- `car_no`：1から連続する車番。
- `observed_at_jst`：**実際にデータを確認した時刻**。例 `2026-11-08T12:00:00+09:00`。後から都合のよい時刻を書かない。
- `deadline_at_jst`／`start_at_jst`：締切・発走時刻。オフセット必須。
- `source_url`：参照元のHTTPS URL。
- `rights_status`：利用許諾を確認した `permitted`、または自分で権利を有する情報の `self_recorded` だけを指定します。**単にブラウザで閲覧できることは許諾ではありません。**
- `line_id`／`line_position`：同ラインに同じラインID、並び位置は1から。
- `rating`：レース開始前に観測した競走得点。
- `recent_win_rate`、`recent_top2_rate`、`recent_top3_rate`：0～1の割合。例：20%=0.20。**全選手で同じ集計期間**の値を使い、前後関係 `win <= top2 <= top3` を守る。将来的には集計窓をメタデータで統一する。
- `age`：レース時点の年齢。

値が不明な欄をゼロや事後情報で埋めず、現時点では取り込みを中止してください。ExcelなどでCSVを開いた場合、日時や先頭ゼロが自動変換されないよう注意してください。

## 3. 締切前にローカル保存

```cmd
py -3 tools\intake_pre_race.py --input local_data\entry_template.csv
```

検証に合格すれば `local_data/pre_race_features.csv` に保存されます。`ingested_at_jst` は実行時刻が自動追記されます。**締切後の遡及登録は拒否**します。同一レースの上書きも拒否します。

`local_data/` はGitで無視する設定です。公開GitHubに第三者由来の個別出走データをアップロードしないでください。

## 4. 確定結果との結合検査

```cmd
py -3 tools\pre_race_readiness.py --features local_data\pre_race_features.csv --output local_data\readiness.json
```

実レースが未確定なら `NOT_READY` は正しい結果です。終了コード2も想定内です。結果が蓄積した後でも、データ充足と独立検証の両方がない限り、予測AIに優位性は認められません。

## 5. テスト

```cmd
py -3 -m unittest discover -s tests -p "test_intake_pre_race.py" -v
```

## 現在の限界

- 手入力は大量の学習には向きません。長期的には、**再利用許諾のあるAPIまたはデータ提供契約**が必要です。
- Kドリームス等をブラウザで閲覧できることは自動収集・再配布の権利ではありません。
- 現在のCSVだけでは学習用サンプルが足りず、着順を正しく予測できる保証もありません。
- `rights_status` は本人申告であり許諾の独立検証を行いません。
- この機能は `paper_trading/verified_models.json` やDiscordに接続せず、購入や利益の判断を行いません。
