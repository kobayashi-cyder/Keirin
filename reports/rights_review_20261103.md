# 2026-11-03 既存索引の未取得結果を3件減少（権利を限定して確認）

## 固定分母・開始時点
- 作業開始時に `data/coverage_summary.json`、`data/pending_races.csv`、`reports/coverage.md` を読み直した。
- 固定対象: `known_listed_races=1409`、`known_missing_result_races=61`（確定結果1347、中止1）。
- 優先日: **2026-11-01**、既存9会場82レース、うち21レース結果収録済み・61レース未解決。新規日付・会場は索引に追加していない。

## 新たに確定結果として収録した3レース
| race_id | 3連単 | 100円払戻 | 確定倍率 | 人気 | 出典 | 独立照合 |
|---|---|---:|---:|---:|---|---|
| `20261101_hofu_12` | 1-5-4 | 2110円 | 21.1 | 7 | https://keirindb.com/keirinjo/hofu/20261101/12r/ | https://keibure.com/63/20261101/ |
| `20261101_kurume_12` | 7-2-4 | 2620円 | 26.2 | 1 | https://keirindb.com/keirinjo/kurume/20261101/12r/ | https://keibure.com/83/20261101/ |
| `20261101_yahiko_12` | 4-5-1 | 15100円 | 151.0 | 45 | https://keirindb.com/keirinjo/yahiko/20261101/12r/ | https://keibure.com/21/20261101/ |

収録先: `data/verified_20261101_12r_three_venues.csv`。3レースとも既存 `pending_races.csv` に `result_recorded=0` で存在していた。各3連単の払戻と組番は上記の2サイトで一致。人気順位は競輪DBで確認。個別Top100オッズは追加していない。

## 権利・取得制限
- 今回は **3つの個別レースに関する数値事実**だけを照合・記録し、サイトの文章・画像・レイアウトは複製していない。競輪DBの https://keirindb.com/privacy-policy/ は文章・画像の無断転載を禁じるが、レース結果の一括データ提供・GitHub公開再配布を明示的に許諾しているわけではない。
- 大量・継続的な複製の権利は **未確認**。残り58レースを同サイトから一括転記する許諾があると解釈していない。今後の大量取得は明示許諾、適法なデータフィード、または再利用条件の明示された別ソースを優先する。
- 再試行可能な候補: https://keirin.jp/ （公式結果・サイトポリシー確認）、https://www.hofukeirin.jp/ （施行者）、https://www.yahikokeirin.com/ （施行者）、https://www.winticket.jp/keirin/results/20261101 （公開結果・再配布条件要確認）、https://www.chariloto.com/keirin/results/21/2026-11-01 （弥彦結果・人気順位は別途要確認）。利用規約・レート制限を守り、制限回避しない。
- 権利照会先の例: 競輪DB https://keirindb.com/privacy-policy/ 記載の問い合わせ先。問い合わせ送信・許諾取得は未実施。
- 残り58件の race_id、理由、候補URL、次の確認事項は `data/source_rights_pending_20261101.csv` に保持。最初の61件はベースラインとして残し、今回の3件のみ `collected_limited_verified` に変更した。

## 数値検証と監査
- 同じ固定1409レース内で `known_missing_result_races: 61 → 58`、**減少3件**。確定結果 `1347 → 1350`、中止1、分母変化0。
- 3件の `race_id` は既存結果CSVと重複なし。各レースの組番は3車一意。100円払戻÷100＝確定倍率を全件確認。
- `Audit historical race coverage` のコミット `150cd79b045955d8169d8e5a876ec5e0be913545` に対応する run `41555087153` の `audit` job は全ステップ `success` と確認（https://github.com/kobayashi-cyder/Keirin/actions/runs/41555087153）。
- 権利台帳の更新コミット `70cc4a846d2df58cc1c4659c6dc9392dcad09680` でも監査が起動するため、最終ステータスは別途確認する。
- 個別人気順オッズは300点、Top100完成0レースのまま。的中車券の確定倍率3件を個別オッズ300点へ加算しない。
