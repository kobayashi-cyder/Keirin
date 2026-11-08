# 保存済みオッズ → 全賭式の着順確率・期待回収率（研究モード）

## この段階でできること

`tools/odds_to_shadow.py` は Windows 上の `local_data/odds_snapshots/<race_id>.jsonl` の**最後の記録**を読み、以下を監査します。

- 全賭式の組番・オッズ・個数が整合しているか（構造監査）。
- サイトが画面に表示したオッズ更新時刻と、実際にHTMLを取得した時刻を区別。
- 3連単が50/210など欠損していた賭式を**計算対象から除外**する。
- 7車なら通常の5賭式、8・9車なら対象となる最大7賭式に対応。

**まだレース前の確率予測AIは実データで検証できていません。** 確率ファイルを与えなければ期待回収率や買い目は表示しません。取得した「オッズ」そのものを的中確率と誤認しません。

## まず実行するコマンド（Webアクセスなし）

```cmd
py -3 tools\odds_to_shadow.py --input local_data\odds_snapshots\7520261108010001.jsonl --output local_data\shadow_matsuyama1.json
```

予測確率がまだ無い場合は、正常に次が表示されます。

```json
{
  "status": "NOT_READY_NO_PROBABILITIES",
  "markets_structurally_complete": ["2shafuku", "2shatan", "3renpuku", "3rentan", "wide"],
  "markets_excluded_incomplete": [],
  "qualifying_count": 0,
  "candidates": [],
  "alert_allowed": false
}
```

※これは実行例です。実際の保存ファイルの状態に応じて、3連単は未完成の可能性があります。表示の時刻とデータ件数を確認してください。

## 将来、時刻付きの予測確率が得られたとき

`--probabilities local_data/models/<race_id>_top3.json` で別ファイルを入力します。確率は**同じレース、レース前に独立して算出**されたものだけを許容します。手作業で「当たりそうな組番」を確率へ置き換えてはいけません。

```json
{
  "schema": "keirin-top3-probabilities-v1",
  "race_id": "7520261108010001",
  "cars": 7,
  "model_id": "RESEARCH_MODEL_NOT_YET_VALIDATED",
  "generated_at": "2026-11-08T17:30:00+09:00",
  "values": {
    "1-2-3": 0.004,
    "...": 0.0
  }
}
```

※上は**形式説明の断片**であり、そのままでは不正なJSON/確率分布です。実際の入力には7車立てなら**全210通り**、9車立てなら**全504通り**の3連単の着順確率を合計1で入力する必要があります。確率ファイルに記録した `generated_at` がオッズ取得時刻より後なら拒否します。自己申告の生成時刻自体を証明できるわけではありません。

実行：

```cmd
py -3 tools\odds_to_shadow.py --input local_data\odds_snapshots\7520261108010001.jsonl --probabilities local_data\models\7520261108010001_top3.json --output local_data\shadow_matsuyama1.json
```

この場合も `status=SHADOW_ONLY_UNVALIDATED`、`alert_allowed=false`、`bet_allowed=false` 固定です。推定的中確率×当時のオッズ下限×仮の安全側係数0.75が1.30以上の場合、**研究用の候補値**を表示するだけです。補正係数0.75は検証済みの統計的保証ではありません。

古いサイト更新時刻、時刻不明、組番の不整合が見つかれば、候補を計算せず安全側に停止します。締切後の保存記録は `HISTORICAL_DO_NOT_TREAT_AS_LIVE` と明記します。

## 検証

```cmd
py -3 -m unittest discover -s tests -p "test_odds_to_shadow.py" -v
```

サイト・Discord・OpenAI APIへは**通信しません**。元のJSONLも書き換えません。研究用レポートはGitで無視される `local_data/` の下だけに保存します。

## 未完了

実運用には、許諾が確認された事前選手情報の収集、時系列学習・独立テスト・確率校正、結果/返還/同着への対応、前向き仮想収支の検証が必要です。ページオッズの取得が完成しても、利益を出せるAIが完成したわけではありません。
