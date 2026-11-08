# OpenAI API × GitHub 仮想投票・自動採点・ChatGPT通知

**研究・仮想投票専用。実際のお金は使いません。**

2026年11月8日時点で、独立した実データ検証済みの予測モデルはありません。GitHubの前向き仮想投票は0件で、的中率・回収率はまだ算出不可です。

## 処理の流れ

1. 合法的に取得した公式の締切前出走表・2車複の全組合せオッズ（7車21点、9車36点）を、取得時刻と共に paper_trading/snapshots にJSONとして保存する。
2. snapshot_ingest.py が「データ取得10分以内、投票締切まで10〜180分、全組合せ完全」を検査し、条件を満たすと2車複1番人気を基準仮想投票として100円分記録。これはAI投票ではなく、利益優位性は主張しない。
3. 別途検証済みの予測モデルができた場合だけ、v0.6で条件に応じて2車複・3連単の優位候補を決定し、inboxに事前記録できる。現在は verified_models.json が空であるため、優位AI投票は無効。
4. ledger.py がGitHub Actionsの実行時刻で事前性を再検証し、journal.jsonl にハッシュ連鎖で追加する。
5. レース確定後、公式払戻の出所と時刻をinboxに登録すると、ledger.py が賭式別・戦略別の的中率と回収率を summary.json に反映する。
6. api_bridge.py が検証済みの新しい優位候補、または新たに採点された結果だけを alerts/feed.json に保存。OpenAI Responses APIは短い説明を補足するだけで、オッズ・確率・買い目・払戻額・回収率を計算しない。
7. 既存のChatGPT「競輪・優位レース監視」タスクがGitHubの alerts/feed.json を確認し、未通知の対象だけChatGPTの通知に流す。APIがChatGPTアプリへ直接プッシュするわけではない。

## APIキーの設定（ユーザー自身の操作が必要）

GitHubのリポジトリ kobayashi-cyder/Keirin → Settings → Secrets and variables → Actions → New repository secret。

- Name: OPENAI_API_KEY
- Value: ご自身のOpenAI APIキー。**チャットや公開GitHubファイルに貼らないこと**。
- 任意のActions変数 OPENAI_MODEL（省略時 gpt-4.1-mini）。

OpenAI APIの請求はChatGPTアプリ契約とは別です。APIの使用量上限や課金設定も確認してください。

キーが無ければAPIの解説文だけ省略し、数値を固定した標準通知文で動きます。APIの応答は予測として利用しません。

## GitHub Actions

.github/workflows/paper-trade-ledger.yml が、snapshot/inboxへのpush・手動・毎時で実行し、事前券生成→台帳記録・採点→API補足→通知フィード→コミットの順で処理します。GitHub Actionsは実行が遅延することがあり、締切前の最新オッズを確実に取り込めるわけではありません。遅れたデータは拒否します。

GITHUB_TOKENの書込権限が無効な場合は、Settings → Actions → General → Workflow permissions を確認してください。ワークフローによる書き込みでは通常のpushイベントが再発火しないため、自己ループしません。

## 結果の提出例（実データではない）

~~~json
{"event_type":"settlement","result":{"race_id":"事前に記録した実レースID","status":"final","confirmed_at_jst":"2026-11-08T15:20:00+09:00","source_url":"https://keirin.jp/...","payouts":{"2shafuku":{"1-2":330},"3rentan":{"1-2-3":2600}}}}
~~~

**上の数値はダミーです。** 実際の結果に流用せず、公式の確定情報に置き換えてください。同着・全額返還などは別途確認が必要です。また、現在の採点コードは入力された払戻を計算しますが、公式の実値との独立自動照合までは実装していません。

## 現在の制約

- Kドリームス等からの無許諾の大量スクレイピングやコピーは行わない。
- 常時利用可能な締切前オッズの供給元は未接続。
- 独立した実データ検証済み確率モデルは未登録。AI優位性の通知は出さない。
- 結果払戻の自動取得・独立照合は未実装。
- ChatGPTの定期タスクは最大1時間に1回。締切の短いレースをリアルタイム確実に通知できない。

現在の実績は paper_trading/summary.json、事前台帳は paper_trading/journal.jsonl、通知は paper_trading/alerts/feed.json を参照してください。

オフラインテスト：python -m unittest discover -s tests -p 'test_api_bridge.py' -v
および test_snapshot_ingest.py, test_paper_ledger.py。
