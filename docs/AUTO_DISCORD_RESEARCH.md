# ローカルオッズを監視してDiscordへ自動通知（研究用）

`tools/auto_discord_research.py` は、別の端末/ウィンドウで実行している `tools/browser_odds_watch.py` が**既に保存した** `local_data/odds_snapshots/*.jsonl` だけを確認します。外部競輪サイトへは一切アクセスしません。**OpenAI APIとDiscordはユーザーが明示指定したときだけ使います。**

## 通知条件

- 7車なら5賭式・8/9車なら7賭式の全買い目を揃えたJSONLがある。
- 最後の観測から10分以内、サイト表示更新から10分以内、締切まで10分以上。
- オッズ、選手名、買い目、候補確率や期待収益は送信しない。**組番の件数・研究進捗だけ**送信する。
- 初回通知成功後は `local_data/discord_research_notified.json` で**レースごとに1回だけ**投稿し、オッズ変動による連投を禁止。
- 通知に失敗した場合、同一監視プロセス内では自動再試行しません。問題を確認して手動で起動し直します。
- 1回のスキャンで最大3件。Webページを増やして取得することはなく、ページのアクセス制限を迂回しない。
- `--send` を付けない限り**絶対にDiscordへ投稿しません**。 `--use-openai` を付けない限りAPI料金もかかりません。

## Windowsで実行

`Keirin-main` 直下でファイル更新後、まず単発のプレビューを行います。

```cmd
py -3 tools\auto_discord_research.py --cycles 1
```

`counts` に `DRY_RUN_READY` があれば、新しいデータ通知の条件を満たしています。残高課金・Webhook投稿なし。

**自動通知**（選択したソースの利用条件を確認した場合のみ）：

```cmd
py -3 tools\auto_discord_research.py --cycles 120 --interval 60 --send
```

60秒おきに120回だけローカルの保存済みJSONLを確認します。これは**既に別ウィンドウで動作しているオッズ取得プログラム**の出力を読むだけです。120回で終了し、Windowsで常時サービスとして起動・登録するものではありません。停止したい場合はCtrl+C。

OpenAIの文章補足も付ける場合（`.env` に `OPENAI_API_KEY` と `DISCORD_WEBHOOK_URL` が必要）：

```cmd
py -3 tools\auto_discord_research.py --cycles 120 --interval 60 --send --use-openai --model gpt-4o
```

1レースにつき最大1度、**送信前に**OpenAIで短い補足文を生成します。APIが失敗した場合はDiscordに投稿しません。キーはGitHubに保存しないでください。GTP-6 Lunaなど別モデルを試すには、そのプロジェクトで実際に利用できるか確認してください。

ローカルの `.env` が `LOCAL_ENV: LOADED` と表示されることを確認します。 `local_data/` の中身や `.env` をGitHubにアップロードしないでください。

### 注意

Discordに `SENT` と表示されても、機械学習モデルが完成した証拠ではありません。**予測の校正・収益性は未検証です。** このツールは、期待回収率130%などの購入判断をDiscordに載せません。

オッズサイトの自動アクセスの許可・頻度については別途確認が必要です。こちらの通知ツールには新しいデータの収集機能はありません。

テスト：

```cmd
py -3 -m unittest discover -s tests -p "test_auto_discord_research.py" -v
```
