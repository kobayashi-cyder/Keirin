# 第1号・着順確率ベースライン（研究専用）

全7賭式エンジンに入力する**1〜3着の同時確率**を、レース前に実際に記録された選手情報から推定する最小モデルです。

## 目的と位置付け

- 線形のPlackett–Luceモデルを、1〜3着の順序尤度で学習する。
- 競走得点・直近の勝率/連対率/3着率・ライン位置・年齢を入力する。
- 開催日単位に学習70%、校正15%、評価15%を時系列分割する。
- バリデーションで温度パラメータを選び、テスト期間に一度だけ適用する。
- テスト期間の上位3着対数損失と、全選手を等確率にした基準値を比較する。
- サードパーティーの機械学習ライブラリ不要（Python 3.11以上）。

**重要：** 順序予測の対数損失が改善しても、全7賭式で利益が出る、あるいは期待回収率130%を達成できることは意味しません。相互作用・ライン・同着・欠場の扱いは未完成で、LightGBM等との優劣も未比較です。

## Windowsコマンド

GitHub最新Keirinのルートから実行：

```cmd
py -3 -m unittest discover -s tests -p "test_train_baseline.py" -v
py -3 tools\train_baseline.py
```

出走前の正規データがまだない場合は、以下が**正しい結果**です：

```json
{
  "status": "NOT_READY",
  "training_allowed": false,
  "eligible_races": 0,
  "distinct_days": 0,
  "excluded_reasons": {"missing_inputs": 1}
}
```

この場合、**モデルは作られず**、実際の競輪を予測しません。

## 学習データの受け入れ

- ユーザーが権利を持つ、または利用許諾があるレース前特徴量を、`tools/intake_pre_race.py` で**締切前に**保存した `local_data/pre_race_features.csv` を使う。提出元、時間、全車の特徴量が揃っている必要がある。
- 既存の `exports/results_3rentan.csv` のレース確定結果と `race_id` で結合する。複数の的中組番（同着）のレースは現段階では除外。
- **500レース以上・開催日30日以上**でなければ学習は実施しない。さらに日付別の各評価期間が少なすぎる場合は拒否する。
- 公開GitHubには `local_data/` の内容や予測モデルをコミットしない（.gitignore）。
- 実データ提供元との利用許諾・保存権限は別途確認が必要。ファイルの `rights_status` は本人申告でありライセンスを自動検証しない。

条件を満たす場合だけ、時系列評価レポートが `local_data/models/research_baseline.json` に保存されます。ステータスは `RESEARCH_BASELINE_EVALUATED` であり、検証済みモデルリストの `paper_trading/verified_models.json` には登録しません。

## 次の工程

1. docs/DATA_PROVIDER_PLAN_20261108.md にある競輪情報APIの提供元へ正式に利用条件・サンプルデータを照会。
2. 取得した実仕様に応じてフィード→`local_data/pre_race_features.csv` の変換器を実装する。
3. 実レース前情報で本モデルと、LightGBMや着時間モデル、ライン共分散モデルの予測精度を比較する。
4. 実オッズ時刻と照合する前向き仮想投票・校正・回収率評価を完了してから、Discordへの優位候補通知を段階的に許可する。
