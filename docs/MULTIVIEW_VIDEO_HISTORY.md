# 多角的な過去情報・過去映像を学習に使う研究基盤

この機能は**3連単の予測を精密化するため、現行6項目に14項目を追加**します。過去の事実のみを出走前に利用できた順序で集計し、学習済みモデルの入力を6次元から20次元へ拡張します。

## 入力の全体像

現在の `local_data/pre_race_features.csv`（競走得点、成績、ライン、年齢）に以下を追加します。

| 視点 | 数値特徴量の例 | 根拠 |
|---|---|---|
| 直近の調子 | 180日の出走回数、平均着順、3着以内率 | 個別の過去確定レース |
| 戦術・映像 | 180日の映像付き出走回数、仕掛け回数、先頭走行秒数、終盤追い上げ、ライン連携スコア | 許諾済み過去動画と検証済み注釈 |
| 開催場の相性 | 365日の同じ競輪場の出走回数 | 過去の開催場・結果 |
| 格付けへの適性 | 同じ競走グレードの出走回数、3着以内率 | 過去のクラス・結果 |
| 疲労・休養 | 前回出走からの経過日数 | 過去出走日時 |
| 走路・当日環境 | バンク周長、風速 | レース前に記録した情報 |

**映像そのものを現在のモデルが自動認識するわけではありません。** まず映像を権限のあるローカルデータとして登録し、必要ならJPEGフレームをローカル抽出します。人が確認した映像由来の戦術ラベルを `rider_events.csv` に書けば、その数値が実際に20次元の学習器へ入力されます。将来の映像認識モデルには別途、動画ラベルの質と学習用GPU・評価が必要です。

## Windowsで空の入力ファイルを作成

GitHubから最新の `build_multiview_features.py`、`video_history.py`、`train_baseline.py`、`predict_pre_race.py` を取得した後、

```cmd
py -3 tools\build_multiview_features.py --init-templates
```

次のファイルに**見出しだけ**作成されます。値が不明なら0にしてはいけません。

- `local_data/history/target_context.csv`: `race_id,car_no,rider_id,venue,grade,bank_length_m,wind_mps,observed_at_jst,source_url,rights_status`
- `local_data/history/rider_events.csv`: `race_id,rider_id,race_at_jst,available_at_jst,venue,grade,finish_position,attack_count,front_seconds,late_gain_places,line_cohesion,video_sha256,source_url,rights_status`

`rider_id` は同じ選手を複数のレースにわたり結びつける、提供元で一意の識別子です。異なる提供元IDを名前の類似だけで結合しないでください。研究用アーカイブに格納する情報について利用許可を確認した場合のみ `rights_status=permitted`、自分の権利に属する観測情報だけの場合は `self_recorded` を記録します。

`race_at_jst` は過去レースの実際の開始日時。 `available_at_jst` はその結果・映像注釈が**実際に利用可能になった日時**で、レース時刻より後である必要があります。`observed_at_jst` の時点で利用可能になっていないデータは、同じ選手の過去記録でも将来情報として**除外**されます。

`attack_count` 等の戦術指標は、映像注釈がある場合だけ入力します。`video_sha256` が空なら4つの戦術指標は**空欄**にします。「映像がない」と「映像で仕掛けが0回だった」は別です。`hist_video_races_180d` という件数が、データの欠損具合を明示します。

`line_cohesion` は0〜1の独自評価値で、採点規約を固定し、後からレース結果を見て有利な値へ修正しないでください。

## 許可された動画を登録

動画ファイルを `local_data\videos\race.mp4` に置いた場合（実際のファイル・URL・IDに置き換える）：

```cmd
py -3 tools\video_history.py --video local_data\videos\race.mp4 --race-id 実レースID --source-url 実際のHTTPS提供元URL --rights-status permitted
```

出力の `sha256_prefix` は動画内容の識別子です。全SHA-256は `local_data/history/video_manifest.jsonl` に保存されます。動画の実体はGitHubに送信されません。

必要なら、別途PCにインストールした **FFmpeg** でローカルJPEGを最大12枚抽出できます。動画登録前に `--frames` を追加して実行します。自動でYouTubeや競輪サイトからダウンロードするものではありません。外部OpenAI APIに映像・フレームを送ることもありません。

抽出フレームを見て戦術を記録し、動画の全SHA-256を `rider_events.csv` の `video_sha256` に入れます。**この結び付けは今の段階では入力者が担います。** 1本の映像を登録しただけで戦術タグが自動生成されたとは言えません。

## 特徴量を生成

```cmd
py -3 tools\build_multiview_features.py
```

出力が `MULTIVIEW_DRY_RUN_VALID` なら入力形式とデータの利用時刻は一応検証済み（ただし外部ライセンスの独立検証ではありません）。保存する場合は以下。

```cmd
py -3 tools\build_multiview_features.py --commit
```

`local_data/pre_race_multiview.csv` に20次元の学習用データを作ります。上書き防止のため同じファイルがある場合は停止します。過去180日以内の有効な出走結果がない選手が1名でもいれば、勝手な平均値を入れずに停止します。

## 20次元モデルを学習・評価

```cmd
py -3 tools\train_baseline.py --features local_data\pre_race_multiview.csv --results exports\results_3rentan.csv --output local_data\models\research_multiview.json --multiview
```

最低500レース・30日以上の、**その時点で利用できた実際の選手データと確定結果**がなければ `NOT_READY` で停止。実際に時系列分割を行い、検証日を使って確率温度を調整し、テスト日で評価します。これは過剰適合しない証明でも利益の保証でもありません。

評価済みの研究モデルが存在するときだけ、出走前に `predict_pre_race.py` に次を指定できます。

```cmd
py -3 tools\predict_pre_race.py --race-id 実レースID --features local_data\pre_race_multiview.csv --model local_data\models\research_multiview.json
```

**以前に取得した過去のレースに、今日新たに注釈した映像を使って「当時の予測」を後付けすることは拒否**します。

出走前予測の精度検証には、先に実装した `tools/backtest_3rentan.py` を使用できます。ただしオッズ・実モデル確率・確定払戻の同一レースID対応が必要です。資金が増える保証は一切ありません。

## 次の開発余地

- バンクの傾斜・直線の長さ、風向、雨量・路面、競走格付け、競走間隔、欠場・落車歴、先行選手との相性、ペース変化を追加可能。ただし出走前に利用可能な事実・統一された定義が前提。
- 画像認識による自動戦術ラベリングは、十分な許諾済み動画・アノテーション・実測性能検証がそろった段階で行う。**動画未取得で「学習済み」とは宣言しない**。
- 公開競輪動画を無断で複製・再配信・外部AIへアップロードしない。ご本人が責任を引き受けても、提供元の利用条件は変わりません。

`.env`、`local_data/videos`、各CSVと動画から抽出した画像を公開GitHubにアップロードしないでください。

オフラインテスト：

```cmd
py -3 -m unittest discover -s tests -p "test_build_multiview_features.py" -v
py -3 -m unittest discover -s tests -p "test_video_history.py" -v
```
