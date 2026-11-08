# レース前データから実際の3連単同時確率を生成する（研究用）

既存の `tools/train_baseline.py` は、適法に取り込んだ**出走前の選手特徴量**と確定結果を使って、Plackett–Luceモデルを学習・時系列分割評価するコードです。新設の `tools/predict_pre_race.py` は、**学習・評価済みのモデル**で、これから開催されるレースの全3連単組番の確率（7車210点／9車504点）を生成します。

**重要:** 2026-11-08時点で、ユーザーのローカルに500レース・30日以上の許諾済み出走前特徴量が揃ったという証拠はありません。AIが完成したと主張できません。学習用データがなければ `FORWARD_MODEL_NOT_READY` と安全に停止します。テストに使う合成データは実予測ではありません。

## ローカルでの正しい順序

1. データ提供元に、**利用・保存・予測学習が許可されていること**を確認する。ブラウザで見られることや、オッズを取得できたことは利用許諾の証拠になりません。
2. `tools/intake_pre_race.py` でレース締切前に7〜9人の特徴量を同じレースIDで取り込み、`local_data/pre_race_features.csv` を蓄積。CSVの項目は `docs/PRE_RACE_INTAKE.md` を参照。**あとから着順を見て事前データを捏造しないこと。**
3. 確定着順が `exports/results_3rentan.csv` と結びつき、最低500レース・30日が揃ったら、学習用・検証用・テスト用に日付単位で分割して、研究モデルを作成します。

```cmd
py -3 tools\train_baseline.py --features local_data\pre_race_features.csv --results exports\results_3rentan.csv --output local_data\models\research_baseline.json
```

`NOT_READY` は正常な防御動作です。テスト期間の平均負の対数尤度が均一確率モデルより悪い場合、新しい予測ファイルは生成しません。ただし均一確率に勝っても、**回収率の優位性が保証されるわけではありません**。

4. 学習に使われていない**未来のレース**の特徴量を締切10分以上前に取り込みます。レースIDはKドリームスの数値形式と合わせてください。
5. 研究予測を生成します。

```cmd
py -3 tools\predict_pre_race.py --race-id 7520261108010001
```

※これはレースIDの**指定形式の例**です。2026-11-08 松山1Rの後から、予測がレース前に存在したかのように出力することはできません。**必ずこれから開催されるレースIDを指定**してください。

出力先は `local_data/models/<race_id>_top3.json`。確率ファイルの `generated_at` は実行時刻で固定し、上書きは禁止。履歴のテスト期間が対象レース日と重なる場合は拒否します。

6. 同じレースのオッズが**その予測生成時刻より後**に保存できたら、既存の研究用期待値エンジンに接続できます。

```cmd
py -3 tools\odds_to_shadow.py --input local_data\odds_snapshots\<race_id>.jsonl --probabilities local_data\models\<race_id>_top3.json
```

ここに表示される `SHADOW_ONLY_UNVALIDATED` は**利益があるという判断ではありません**。以前保存したオッズよりあとに予測を生成した場合は、時系列が逆になるため拒否されます。

## 何を計算しないか

- GPT-4o/ChatGPTにオッズから「それっぽい確率」を作らせません。予測値は実際の出走前特徴量と、保存済み学習モデルの数値演算のみから生成します。
- Discordへ勝ち目・買い目・期待収益を配信しません。自動通知の段階ではレースIDとオッズ件数の**品質ステータスだけ**送ります。
- 同着・返還・欠場などの特殊な的中ルール、前向き仮想収支の有意性、許諾済み配信契約の検証は未完了です。

学習が完全になっても、研究予測には `model_verified=false` / `alert_allowed=false` / `bet_allowed=false` を明記します。

テスト：

```cmd
py -3 -m unittest discover -s tests -p "test_predict_pre_race.py" -v
```

## オッズ取得・Discord自動通知との関係

`tools/auto_discord_research.py` は**研究用のデータ品質状況だけ**を投稿します。 `predict_pre_race.py` はOpenAI APIもDiscordも呼びません。異なる権利範囲のデータや未校正の確率を誤って混ぜないため、役割を分離しています。
