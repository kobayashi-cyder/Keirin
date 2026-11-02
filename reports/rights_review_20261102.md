# 2026-11-02 既存索引の未取得結果・権利確認記録

## 取得前の固定基準（main の coverage_summary.json / pending_races.csv / coverage.md）
- 対象: 2020-01-01〜2026-11-02。索引済み **1409** レース、確定結果 **1347**、中止 **1**、既知結果・状態未解決 **61**。
- 優先日: **2026-11-01**。索引済み **82** レース、既存結果 **21**、未解決 **61**。未解決の内訳: 防府12、久留米12、松戸7、小田原7、宇都宮11、弥彦12。
- 新規開催日・会場は**追加しない**。このスナップショットは将来の取得進捗により変更しない。
- 未取得61件のrace_id、候補ソース、権利状態は `data/source_rights_pending_20261101.csv` に記録。これは**結果CSVではなく**、組番・払戻・倍率・人気順位を含まない。

## 今回確認したソースと制限
1. 楽天Kドリームス: [2026-11-01開催一覧](https://keirin.kdreams.jp/kaisai/2026/11/01/) および [サイト利用条件](https://keirin.kdreams.jp/info/sitepolicy/) を確認。サイト利用条件は無断転載・コピーを禁止。公開ページの閲覧はGitHubでの61レース一括再配布許諾ではない。**結果値の新規転記なし**。
2. KEIRIN.JP: [サイトポリシー](https://keirin.jp/pc/dfw/portal/guest/policy/index.html) は複製・転用等を原則制限。**許諾未確認**。
3. WINTICKET: [2026-11-01結果一覧](https://www.winticket.jp/keirin/results/20261101) と [利用規約](https://www.winticket.jp/terms) を候補として確認。GitHub公開再配布の明示許諾を確認できず、**リンクのみ**。
4. 施行者公式の候補: [防府競輪](https://www.hofukeirin.jp/)、[宇都宮競輪・結果アーカイブ](https://utsunomiya-keirin.jp/results/)、[弥彦競輪](https://www.yahikokeirin.com/) 。これらの結果ページからの機械取得・再配布の許諾は未確認。宇都宮の結果アーカイブは KEIRIN.JP へのリンクを含む。
5. [JKA情報公開](https://www.keirin-autorace.or.jp/disclosure/shiryou.html) に公開資料はあるが、対象日の全61レースについて3連単払戻・人気順位の再配布可能な公開CSVは確認できなかった。

## 再開条件と照会事項
- 公開再配布可能な公式データ提供、明示的な利用許諾、または適法な代替供給元を確認してから結果値を収録する。公開結果の存在だけでは許諾とみなさない。
- 照会内容: 2020年以降の各競輪場・レースの3連単確定組番、100円払戻、人気順位の機械取得・保存・GitHub上での公開再配布の可否、API/ファイル提供条件、出典表示、頻度・レート制限、同着/中止/返還の取扱い、利用料。
- 許諾取得後は2026-11-01の**既存61レースだけ**を先に回収し、`race_id` 重複、的中組番の一意性（同着は複数組番）、払戻÷100＝確定倍率、人気順位・出典URLを検証。中止/全返還は結果値を捏造せず別管理。
- `Audit historical race coverage` を通し、取得前の **1409 / 61** と取得後の同一索引範囲の残数を比較。Top100個別オッズは結果収録と別計数。

## 実績
- 2026-11-02: **新規確定結果0レース、既知結果残件61→61（減少0件）**。権利未確認ソースからの大量複製を行わず、未取得箇所を固定CSVに記録。
- 未取得結果の値は推測・補完していない。問い合わせ送信や再配布許諾の取得は**未実施**。

## GitHub Actions 監査の証跡
- `Audit historical race coverage` の対象コード更新後、`github-actions[bot]` による [自動再計算コミット](https://github.com/kobayashi-cyder/Keirin/commit/11a826e52542d1fad7e958d5986065c7575f09d7) を確認。ワークフローは Unit tests → カバレッジ再計算 → 分析表再構築 → 自動コミットの順であり、最終ステップによる `reports/coverage.md` 更新を確認した。
- 更新後の `coverage_summary.json`: `known_listed_races=1409`、`known_missing_result_races=61`、`individual_odds_points=300`。権利確認待ちCSVの61件は全件 `pending_races.csv` の同日未取得IDに一致し、重複なし。
- 実行ID・Actions UIの最終 `success` 表示自体は取得できなかったため、確認できた事実は自動更新コミットまでとする。
