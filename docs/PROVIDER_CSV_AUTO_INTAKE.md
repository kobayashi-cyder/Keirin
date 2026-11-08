# 出走前レースカードCSV → 予測AIの学習用アーカイブ（自動変換）

**新しい `tools/import_provider_csv.py` は、実際に提供されたCSVを既存の15列へ自動変換し、出走前データの厳格な検証と追記を行います。** 大量の手入力は不要です。外部サイトへのアクセスもOpenAI APIも実行しません。

## 何が自動化されたか

提供元CSVの次の別名を認識します。

| 正規化先 | 対応する主な列名 |
|---|---|
| レースID | `race_id`, `raceId`, `レースID` |
| 車番 | `car_no`, `banum`, `car`, `車番` |
| 競走得点 | `rating`, `race_score`, `score`, `競走得点` |
| 勝率 | `recent_win_rate`, `win_rate`, `勝率` |
| 2連対率 | `recent_top2_rate`, `place2_rate`, `2連対率` |
| 3連対率 | `recent_top3_rate`, `place3_rate`, `3連対率` |
| ライン | `line_id`, `line`, `ライン` |
| ライン位置 | `line_position`, `line_order`, `番手` |
| 年齢 | `age`, `年齢` |

率は `0.45`, `45.0`, `45.0%`, `45.0％` を `0.45` に正規化します。ちょうど `1.0` は規定通り100%と扱います。提供元の単位は事前に確認してください。

レースID、締切・発走時刻、実際の提供元URL、データの保存・モデル学習に関する権限、**実際のラインと位置**が必要です。足りない場合は推測せず拒否します。取り込み時刻をシステム時計で自動付与するので、終了した過去レースのCSVに「以前の出走前取得日時」を後付けできません。

## Windowsで使う

CSVを `local_data\provider\racecards.csv` に保存します。**実在する提供元の、利用・保存・学習権限を確認したCSVだけを用いてください。** HTMLの画像から捏造したり、結果を見て過去の出走前データを組み立てないこと。

提供元CSVに必要項目とライン情報・締切・発走時刻がある場合（デフォルトは保存しない検証）：

```cmd
py -3 tools\import_provider_csv.py --input local_data\provider\racecards.csv --source-url "https://provider.example.com/export" --rights-status permitted
```

（上のURLは**書式説明用**。利用権限がある実際のHTTPS URLへ置き換えてください。）

**1レースだけ**で、提供元にライン情報と締切・発走時刻がなく、手元で正確に確認できる場合は引数で与えられます。

```cmd
py -3 tools\import_provider_csv.py --input local_data\provider\racecards.csv --race-id 7520261109010001 --deadline-jst "2026-11-09T20:35:00+09:00" --start-jst "2026-11-09T20:40:00+09:00" --lineup "1-2-6/7/3-4-5" --source-url "https://provider.example.com/racecards" --rights-status permitted
```

※このレースID・締切・発走時刻・ラインは**書式説明用の架空例**です。そのまま実行しないでください。締切と並びは実際の提供元データと照合してください。**観測後、締切前**でなければ取り込めません。指定IDがCSVに既存のIDと矛盾すれば拒否します。

`"status": "DRY_RUN_VALIDATED_NOT_SAVED"` なら形式検証は合格です。保存してよいか確認した上で、末尾に **`--commit`** をつけて再実行します。

成功時に `local_data/pre_race_features.csv` へ**追記のみ**で保存されます。重複レースの上書きは行いません。出走前データが無い場合は `missing required provider field ...` と項目名だけを表示し、空欄を0で補完しません。

## 任意の独自CSV列への対応

提供元の列名が違う場合は、ローカルJSONで対応を明記できます。

`local_data/provider/columns.json` の例：

```json
{
  "rating": "競走スコア",
  "recent_win_rate": "最近勝率",
  "recent_top2_rate": "最近連対率",
  "recent_top3_rate": "最近３連対率",
  "line_id": "ライン識別子",
  "line_position": "ライン番手"
}
```

```cmd
py -3 tools\import_provider_csv.py --input local_data\provider\racecards.csv --mapping local_data\provider\columns.json --source-url "https://provider.example.com/export" --rights-status permitted
```

列名が一致しないときは `--mapping` に対応を書く必要があります。これはAPI仕様を推測で書いたものではありません。

## 蓄積からモデル学習へ

```cmd
py -3 tools\pre_race_readiness.py --features local_data\pre_race_features.csv --results exports\results_3rentan.csv
```

この結果に示される**実際に照合できた500レース以上・30日以上**を学習開始の最低条件とし、研究モデルを時系列分割して評価します。データ件数に達しても、収益性や利用権限を自動証明するものではありません。予測モデルの成績が未検証の段階ではDiscordへの買い目通知は停止したままです。

## 外部データの制約

- 楽天Kドリームスのサイトポリシーでは無断コピー等を禁止しており、ユーザーが責任を負う意思があっても、提供元の利用条件は変わりません。
- デルカは競輪情報APIの保有を公式に述べていますが、個人向け提供条件・料金・仕様・ML利用可否は未確認です。
- 公開GitHubのデータファイルが閲覧できることと、予測AI学習への利用許諾があることは別です。
- `rights_status=permitted` は**利用権限を確認した本人の申告**であり、プログラムが独立してライセンスを認定するものではありません。
- `local_data/` と `.env` はGitHubにアップロードしないでください。

オフラインテスト：

```cmd
py -3 -m unittest discover -s tests -p "test_import_provider_csv.py" -v
```

関連：[デルカ公式](https://deruca.com/) / [楽天Kドリームスのサイトポリシー](https://keirin.kdreams.jp/info/sitepolicy/)
