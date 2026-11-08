# OpenAI GPT-6 Luna API × Discord Webhook（Windows）

`tools/discord_openai_status.py` は **OpenAI Responses API の `gpt-6-luna`** に短い日本語の研究状況コメントを生成させ、DiscordのWebhookへ1件投稿するWindows用ツールです。OpenAIに実際の予想、的中確率、オッズ、買い目の決定をさせません。

2026-11-08時点で検証済み着順予測モデルがないため、**接続テスト**と**保存済みオッズのデータ品質状態**だけを投稿します。候補の期待回収率があっても公開しません。OpenAI補足は完全に省略可能です。

## 初期設定

- OpenAI Platformで **APIキー** を作成し、API請求設定と使用上限を確認する。ChatGPT Plus等の料金とAPI料金は別です。GPT-6 LunaのAPIモデルIDは **`gpt-6-luna`**。
- Discordで自分のサーバー → サーバー設定 → 連携サービス → ウェブフック → ウェブフックを作成。投稿先チャンネルを選択し、**Webhook URLをコピー**する。
- Webhook URLとAPIキーは**どちらも秘密情報**。ChatGPT・GitHub・スクリーンショットに貼らない。漏えいしたらWebhook削除とAPIキー失効・再発行を行う。

Python 3.11以上。外部pipパッケージの追加は不要です。コードで既定モデルを `gpt-6-luna` に固定しています。短文生成に適した `reasoning.effort=none` を使います。

## 検証・送信コマンド（Keirin-mainフォルダ）

最初は通信のないプレビュー：

```cmd
py -3 tools\discord_openai_status.py --test
```

OpenAIの生成文を確認するだけ（API料金が発生する場合があります。Discordには投稿しません）：

```cmd
py -3 tools\discord_openai_status.py --test --use-openai
```

**OpenAI API → Discord Webhook の接続テスト：**

```cmd
py -3 tools\discord_openai_status.py --test --use-openai --send
```

- 表示された `OpenAI APIキー（画面には表示されません）:` にAPIキーを貼り付けてEnter。
- 表示された `Discord Webhook URL（画面には表示されません）:` にWebhook URLを貼り付けてEnter。
- `OPENAI_RESULT: OK` と `DISCORD_RESULT: SENT` が両方出れば、GPT-6 Lunaが補足を生成し、Discordに投稿できました。
- `OPENAI_RESULT: HTTP_401` ならAPIキー、`HTTP_429` なら使用枠・課金設定等を確認。Discord側 `RATE_LIMITED` なら送信間隔をあけてください。APIが失敗しても固定のテスト文だけDiscordに届くことはあります。

APIキーとWebhook URLは画面に表示されない入力方式です。入力値はローカルファイルへ保存しません。再実行時に再入力するか、利用者自身が安全な環境変数として設定してください。コマンド引数に秘密情報を直接書かないでください。

### 保存済み松山1Rの「データ品質」報告を投稿

すでに作成済みの `local_data\shadow_matsuyama1.json` を利用します。

```cmd
py -3 tools\discord_openai_status.py --report local_data\shadow_matsuyama1.json
```

上記は送信なしのプレビューです。実際にDiscordへ1件投稿する場合：

```cmd
py -3 tools\discord_openai_status.py --report local_data\shadow_matsuyama1.json --use-openai --send
```

研究レポートの `candidates`、オッズ、確率、その他の任意フィールドはDiscordにもOpenAIにも転送しません。モデルに渡すのは `kind` と `prediction_model_verified=false` の技術状態のみです。Discordには固定されたレースIDと賭式別の件数だけを載せます。Discord投稿時のメンションを無効にしています。**このプログラムは1実行で最大1回Discordに投稿するだけ**で、10秒ごと自動連投しません。

## トラブル対応・境界

- OpenAI APIのモデルは環境変数 `OPENAI_MODEL` で変更できますが、標準は `gpt-6-luna` です。GPT-6 LunaはOpenAI Responses APIに対応しています。
- APIキー未設定、ネットワーク障害、出力の禁止語などの場合はAIの補足を無効にします。数字・買い目・賭式等をAIに自由作文させません。
- APIからの出力の正確性は検証が必要です。接続テストの成功はAI予測の精度を保証しません。
- 既存の `paper_trading/api_bridge.py` はGitHub台帳の別経路です。本ツールは**Windowsローカルの研究報告だけ**を送信する独立ツールで、GitHub Actionsや既存の検証済みモデルゲートを迂回しません。
- オッズ収集元の利用条件、データの公開・再配布権限は別途確認が必要です。

オフラインテスト：

```cmd
py -3 -m unittest discover -s tests -p "test_discord_openai_status.py" -v
```

公式の参考資料：

- GPT-6 Luna API: https://developers.openai.com/api/docs/models/gpt-6-luna
- Discord Webhook作成: https://support.discord.com/hc/ja/articles/228383668
