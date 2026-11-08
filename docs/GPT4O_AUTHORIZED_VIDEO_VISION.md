# GPT-4oで競輪の映像を自動解析する（利用権限のあるローカル映像）

**新実装** \`tools/gpt4o_race_vision.py\` は、既存の \`OPENAI_API_KEY\` を利用し、適法に取得した**ローカルの単独レース映像**を画像フレームに分解して、GPT-4oの視覚認識で車番・攻め・先頭・追い上げ・ライン連携を自動評価する研究用処理です。

### できることと、現時点でできないこと

| 処理 | 対応 |
|---|---|
| \`local_data/videos/\` の利用権限が確認されたレース動画 | ✅ 実装 |
| FFmpegで動画から画像を最大24枚抽出（RAM内、JPEGファイルに保存しない） | ✅ 実装 |
| OpenAI **Responses API / gpt-4o** に画像を入力し構造化JSONを生成 | ✅ 実装 |
| 不明な車番・秒数・戦術を \`null\` にする | ✅ 実装 |
| 原レースの登録SHA-256・動画出典・分析日時の記録 | ✅ 実装 |
| 実際の公式着順・選手IDとの照合、研究用学習CSVへの取り込み | ✅ 厳しい安全条件を満たす場合のみ |
| \`https://www.youtube.com/watch?v=...\` のURLだけをGPT-4oへ渡し動画を読む | ❌ 非対応 |
| YouTube映像のスクレイピング／無許可のダウンロード／再生画面のキャプチャ | ❌ 行わない |
| レース全体を確実に把握したかという外部評価、着順AIの利益保証 | ❌ 未検証 |

**OpenAI APIはYouTube動画ページのURLを入力するだけでは映像そのものを見られません。** 今回は公式YouTubeサイトやYouTube再生画面から無断でフレームを取得しません。外部モデルにフレームを送ることに必要な権限を持った動画だけが対象です。例えば、自分が撮影して権利を持つ映像、配信元から分析・第三者AI送信の許可を得ている映像を用意してください。

## インストール（Windows CMD、Keirin-main）

最新版に更新：

~~~cmd
curl.exe -fL "https://raw.githubusercontent.com/kobayashi-cyder/Keirin/main/tools/gpt4o_race_vision.py" -o "tools\gpt4o_race_vision.py"
curl.exe -fL "https://raw.githubusercontent.com/kobayashi-cyder/Keirin/main/tools/video_history.py" -o "tools\video_history.py"
curl.exe -fL "https://raw.githubusercontent.com/kobayashi-cyder/Keirin/main/tools/build_multiview_features.py" -o "tools\build_multiview_features.py"
~~~

Python 3.11以上の標準ライブラリだけで動きます。映像を扱うには **FFmpeg/FFprobe** がWindowsのPATHに必要です（\`ffmpeg -version\`, \`ffprobe -version\` で確認）。OpenAIのPython SDKは不要です。

設定済みの \`.env\` に \`OPENAI_API_KEY=...\` が必要です。既存のGPT-4o APIキーを使用してください。**APIキーをチャットやGitHubへ送らないでください。** GPT-4oの画像入力・応答出力トークンは有料です。枚数を増やすほど費用も増えます。

## Step 1: 動画を権利付きローカル素材として登録

実際に利用可能な短い単独レースMP4を \`local_data/videos/race.mp4\` として保存済みの場合：

~~~cmd
py -3 tools\video_history.py --video local_data\videos\race.mp4 --race-id 実際の数字レースID --source-url 実際のHTTPS出典URL --rights-status permitted
~~~

正当な権利を自ら保有している動画には \`self_recorded\` を使えます。**上記のオプションは利用権限を自己申告するだけで、提供元から権限が発行された証拠になるわけではありません。** 承認なしの外部AI送信は行わないでください。

登録すると動画のSHA-256が \`local_data/history/video_manifest.jsonl\` に記録され、ファイル差し替えを検知できます。

## Step 2: API料金なしで確認

~~~cmd
py -3 tools\gpt4o_race_vision.py --video local_data\videos\race.mp4 --race-id 実際の数字レースID
~~~

\`DRY_RUN_NO_API_CALL\` と対象フレームの時刻が表示されます。動画1本は20～900秒を要求し、最大24フレームに制限します。長時間の開催全体の動画は受け付けません。

## Step 3: GPT-4oで実際に自動解析（有料）

正当に保持する動画フレームをOpenAIに送ることを確認してから実行します：

~~~cmd
py -3 tools\gpt4o_race_vision.py --video local_data\videos\race.mp4 --race-id 実際の数字レースID --analyze --allow-frame-upload --acknowledge-media-rights --frame-count 12
~~~

このコマンドを実行したときだけ**1回のOpenAI Responses APIリクエスト**が発生します。最大24枚のJPEGをbase64化して送り、動画そのものはアップロードしません。フレームをOpenAIへ送信するので、動画の権利者がこの処理を認めていることが必要です。

結果は \`local_data/vision_reports/<race_id>.json\` に保存します。既存ファイルの上書きは拒否。出力内の主なフィールド：

- \`model: "gpt-4o"\`：4oを使用。
- \`prediction.riders[]\`：車番ごとの \`attack_count\`, \`front_seconds\`, \`late_gain_places\`, \`line_cohesion\` の候補。
- \`prediction.riders[].evidence_seconds\`：何秒の画像にその観察の根拠があるか。
- \`prediction.riders[].confidence\`：モデル自己評価値（実際の正答率ではありません）。
- \`null\`：観察できなかった値。0回だったとは解釈しません。
- \`generated_at_jst\`：実際のAPI解析日時。結果の過去への捏造を避けます。
- \`status: AI_CANDIDATE_REQUIRES_VERIFICATION\`、\`model_verified: false\`：研究用の暫定候補。

**特に注意：** 12～24枚の静止画像だけでは、先頭走行時間を秒単位で正確に復元できない場合が多く、GPT-4oが値を出しても正しいとは限りません。完全なレース分析が確認できない場合は \`ai_suggested_training_candidate: false\` で、CSV学習データへの取り込みを止めます。

## Step 4: 公式確定結果と選手IDで照合して、研究用CSVに自動投入

公式データや適切に取得したライセンス済みレース結果を事前に \`local_data/history/verified_race_results.csv\` に保存済みの場合、以下を実行：

~~~cmd
py -3 tools\gpt4o_race_vision.py --commit-report local_data\vision_reports\実際の数字レースID.json --official-results local_data\history\verified_race_results.csv
~~~

照合用CSVは**全車分**を必要とし、列順は以下に固定されています：

~~~csv
race_id,race_at_jst,venue,grade,car_no,rider_id,finish_position,source_url,rights_status
~~~

各行には**実際の**車番・選手ID・確定着順を出典とともに記録します。同着・欠場・未確定・レース間違いのデータは拒否します。成績・身元の欄をGPT-4oの想像で補わず、外部の確定ラベルからのみ取得します。

注釈JSONについて全車一致、90%以上のモデル自己信頼度、各選手2点以上の入力画像タイムスタンプ、全指標が欠損なし、モデルのレース識別・カバレッジの自己判定あり、といった条件を満たすときだけ \`local_data/history/gpt4o_rider_events.csv\` に研究用行を追加します。

**重要：** これらの条件を満たしても、AIの映像解釈が正しいと実証されたわけではありません。自動追加された行は \`GPT4O_UNVERIFIED_RESEARCH_ROWS_APPENDED\` と明示され、ベットや通知の許可には使用しません。予測精度を認定するには、人手で判定した基準動画と照合する追加検証が必要です。

## Step 5: 既存20次元モデルにつなぐ

別途、将来レースの**予測時点で保存済み**の出走情報 \`local_data/pre_race_features.csv\` と、実際の選手IDを紐づけた \`local_data/history/target_context.csv\` が必要です。

~~~cmd
py -3 tools\build_multiview_features.py --events local_data\history\gpt4o_rider_events.csv
~~~

検査に合格すれば \`--commit\` を付けて \`local_data/pre_race_multiview.csv\` に保存できます。

最低500レース・30日以上の時系列研究データがなければ、既存の研究用モデルは学習停止します。過去映像を**今日**解析した情報を、**過去の事前予測に利用したことにしない**という時刻検証は維持されます。

## セキュリティ／コスト

- 解析・CSV変換はローカルのみ実行。OpenAI画像リクエストは明示したときだけ。
- \`store: false\` を使いAPI側の保存を最小化します（プラットフォームの通常のセキュリティ・ログ保持条件は別途適用）。
- 動画・画像・認証情報・出走前CSV・GPT-4oの結果ファイルは \`local_data/\` と \`.env\` に留め、公開GitHubにはコミットしません。
- URLから動画をダウンロードする手順は含みません。
- モデルの自己評価90%は正答率90%の意味ではありません。

APIの根拠：
- [OpenAI画像入力](https://developers.openai.com/api/docs/guides/images-vision)
- [OpenAI構造化出力](https://developers.openai.com/api/docs/guides/structured-outputs)
- [YouTube APIサービス開発者ポリシー](https://developers.google.com/youtube/terms/developer-policies)

オフライン単体テスト：

~~~cmd
py -3 -m unittest discover -s tests -p "test_gpt4o_race_vision.py" -v
~~~
