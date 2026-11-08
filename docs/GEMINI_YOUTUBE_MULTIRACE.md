# 任意の競輪レースをGeminiで多重・並列解析（YouTube URL直接、MP4不要）

新規: \`tools/gemini_youtube_multirace.py\`

Google公式の [Gemini Video Understanding](https://ai.google.dev/gemini-api/docs/video-understanding) が提供する**公開YouTube URL直接入力**を使います。**MP4、yt-dlp、画面録画、YouTubeからのフレーム保存は不要**です。これはGemini APIがアクセス可能な公開動画だけに対応するプレビュー機能で、レース映像を確実に認識できる保証はありません。

## 実装した3つの「多重化」

1. **任意のレース**：固定の松山1Rから解放し、\`race_id / race_date / venue / venue_key / race_number / cars\` を毎回指定。
2. **複数のレースを同時に処理**：\`--jobs local_data/history/gemini_queue.csv\` に20件以下の対象を記載。\`--workers 1..3\` で同時リクエスト上限を設定。プロジェクトのGemini API上限と料金は別途適用されます。
3. **同じ動画を独立に2～3回確認**：\`--passes 2\` が標準。車番・対象レースの時刻・戦術数値が一致した場合だけ研究用候補とし、食い違う値はnullにします。**同じモデルの複数回解析は独立した事実確認ではなく、同じ誤認を共有する可能性があります。**

また、YouTube動画URLを省略して \`--auto-discover\` を付ければ、**YouTube Data APIの公式検索**から候補URLを自動で探してGeminiに渡します。候補動画の内容を正しいレースと決めつけず、毎回Geminiに対象レースの映像・日時・R番号を確認させます。検索がヒットしない、複数レース配信で対象が判別できない場合は、そのレースの数値を作りません。

### 0. Windowsの準備

既存の \`Keirin-main\` CMDで：

~~~cmd
curl.exe -fL "https://raw.githubusercontent.com/kobayashi-cyder/Keirin/main/tools/gemini_youtube_multirace.py" -o "tools\gemini_youtube_multirace.py"
curl.exe -fL "https://raw.githubusercontent.com/kobayashi-cyder/Keirin/main/tools/build_multiview_features.py" -o "tools\build_multiview_features.py"
curl.exe -fL "https://raw.githubusercontent.com/kobayashi-cyder/Keirin/main/tools/youtube_race_search.py" -o "tools\youtube_race_search.py"
curl.exe -fL "https://raw.githubusercontent.com/kobayashi-cyder/Keirin/main/tools/gpt4o_race_vision.py" -o "tools\gpt4o_race_vision.py"
py -3 -m pip install -U google-genai
~~~

Google AI Studioで取得したGemini APIキーを、GitHub管理外の \`.env\` に追加します。YouTubeの候補を自動検索する場合は別に \`YOUTUBE_API_KEY\` も必要です。

~~~text
GEMINI_API_KEY=あなたのGoogleAIStudioのAPIキー
YOUTUBE_API_KEY=あなたのYouTubeDataAPIのAPIキー
~~~

\`OPENAI_API_KEY\` とは別のキーです。キーを公開GitHub、ChatGPT、Discordへ送らないでください。

### 1. 1本のレースを直接指定（dry runは通信・課金なし）

ユーザーが確認した公開YouTube動画URLがある場合の書式：

~~~cmd
py -3 tools\gemini_youtube_multirace.py --race-id 7520261108010001 --race-date 2026-11-08 --venue 松山 --venue-key matsuyama --race-number 1 --cars 7 --video-url "https://www.youtube.com/watch?v=BhwmwNby6Ow"
~~~

このコマンドでは検索・Gemini API呼び出しをしません。単にパラメータと解析計画を表示します。

実際に解析する場合、**意図的にAPI呼び出しを許可**します：

~~~cmd
py -3 tools\gemini_youtube_multirace.py --race-id 7520261108010001 --race-date 2026-11-08 --venue 松山 --venue-key matsuyama --race-number 1 --cars 7 --video-url "https://www.youtube.com/watch?v=BhwmwNby6Ow" --analyze --confirm-api-cost --passes 2 --workers 2 --max-api-calls 2
~~~

このIDと日時はあくまで入力書式例です。松山1Rの特定・視覚解析の成否は実行結果でしか確認できません。メタデータだけで正しいレースとは断定しません。

### 2. 映像URLすら手動で探したくない場合

**登録済み動画がなくても**、開催日・競輪場・R番号から公式YouTube検索を実行して候補を探せます。

~~~cmd
py -3 tools\gemini_youtube_multirace.py --race-id 7520261108010001 --race-date 2026-11-08 --venue 松山 --venue-key matsuyama --race-number 1 --cars 7 --analyze --confirm-api-cost --auto-discover --candidates-per-race 1 --max-searches 1 --passes 2 --workers 2 --max-api-calls 2
~~~

この実行ではYouTube Data APIの \`search.list\` が1回、Geminiの映像解析が最大2回行われます。候補が誤レースの場合、Geminiが否定すれば除外します。Geminiが誤って一致を主張する可能性は残るため、出力はあくまで**研究用候補**です。

### 3. 複数のレースを一度に並列解析

最初に空のキューを作成：

~~~cmd
py -3 tools\gemini_youtube_multirace.py --init-template local_data\history\gemini_queue.csv
notepad local_data\history\gemini_queue.csv
~~~

CSVは次の列順です：

~~~csv
race_id,race_date,venue,venue_key,race_number,cars,youtube_url
~~~

例（**実在する開催データ・車数・レースID・候補URLを確認して埋めてください**）：

~~~text
7520261108010001,2026-11-08,松山,matsuyama,1,7,https://www.youtube.com/watch?v=BhwmwNby6Ow
~~~

※\`youtube_url\` を空にした行は、\`--auto-discover\` があれば公式YouTube検索で候補を自動取得します。動画URLが未確認でも、その分の解析計画を保守的に計上します。

~~~cmd
py -3 tools\gemini_youtube_multirace.py --jobs local_data\history\gemini_queue.csv
~~~

有料・API利用枠を消費するバッチ実行：

~~~cmd
py -3 tools\gemini_youtube_multirace.py --jobs local_data\history\gemini_queue.csv --analyze --confirm-api-cost --auto-discover --passes 2 --workers 2 --max-api-calls 8 --max-searches 4 --candidates-per-race 1
~~~

**同時実行と予算は別です**。\`--workers 2\` は最大2リクエストを同時に送ります。\`--max-api-calls 8\` はこのバッチの最大Gemini呼び出し計画を8回に抑えます。レート制限（RPM/TPM/RPD）への抵触やAPIの実際の課金額は保証されません。429等で失敗しても**勝手に何度も再送せず**、そのパスを保存しないまま失敗として返します。

実行に成功した各パスは、直ちに次の場所に保存します。

- \`local_data/gemini_runs/レースID_動画ID_pass1.json\`
- \`local_data/gemini_runs/レースID_動画ID_pass2.json\`
- \`local_data/gemini_reports/レースID_動画ID.json\`

途中で中断しても、**保存済みの同一パスは再実行せず読み込みます**。ただしAPIが完了した直後、保存する前にPCが落ちた場合などは重複課金を完全には防げません。

### 4. 研究用CSVへ自動取り込み

実際の確定結果から取得した車番別・選手ID別・着順別の正式なデータが \`local_data/history/verified_race_results.csv\` にあることが必要です。形式：

~~~csv
race_id,race_at_jst,venue,grade,car_no,rider_id,finish_position,source_url,rights_status
~~~

\`venue\` はキューの \`venue_key\` と同じ識別子にし、1つのレースに7～9選手分を揃え、実際に得た \`race_at_jst\` と着順を記録してください。公式結果の収集自体は、今回は実装していません（権利とデータ提供元の契約が必要です）。

同じバッチコマンドに下記のフラグを付ければ、解析に**完全合意が成立**し、別の公式結果CSVと一致するものだけを研究用CSVに追記します。

~~~cmd
--export-research --official-results local_data\history\verified_race_results.csv
~~~

追加先：

- \`local_data/history/gemini_rider_events.csv\`：実験用の戦術・結果の車番別行
- \`local_data/history/gemini_research_links.csv\`：Gemini自動解析由来という出典と時刻

\`review_status=AUTOMATED_RESEARCH_ONLY\` は手動確認ではなく、**AIが同じ結果を2回以上出しただけ**という意味です。競輪の戦術を正確に理解したという第三者検証ではありません。

予測用の20次元特徴量へ実験的に含める場合、明示的なオプトインが必要です：

~~~cmd
py -3 tools\build_multiview_features.py --events local_data\history\gemini_rider_events.csv --youtube-index local_data\history\gemini_research_links.csv --allow-ai-research-annotations
~~~

この出力が \`MULTIVIEW_DRY_RUN_VALID\` で、学習対象の **各選手に180日以内の確定履歴** があり、時系列が合う場合だけ \`--commit\` で書き込みます。Geminiによる歴史データは、その解析・エクスポート**以前のレースの事前予測に使わない**設定です。

### 5. 手放しの毎日実行

事前にキューのCSVとAPIキーを準備できていれば、Windowsのタスク スケジューラから上の\`--jobs ... --analyze ...\` コマンドを毎日実行できます。新しいキュー行の追加は別途提供元データや登録処理が必要です。**未登録の任意のレースIDを自動生成したり、確定結果を推測したりすることはありません**。

### 動画解析の品質リスクと扱い

Geminiの動画解析は高速なレースのすべてのフレームを完全追跡するわけではありません（公式ドキュメントには既定で1fpsのサンプリング説明あり）。**複数パスの結果が一致しても正解ではなく、レースIDの誤同定や車番混同が残る可能性があります。** 数値の一致だけで自動投票やDiscord予想通知を解放しません。

- 完全一致に失敗した車番・数値は\`null\`、レースは\`INCOMPLETE_OR_DISAGREED_NOT_TRAINING_READY\`で隔離。
- 正式な結果データがなければ戦術のCSV exportは実施しません。
- AI自身のconfidence≥0.9は客観的正答率90%ではありません。
- APIキーや結果は \`.env\` と \`local_data/\` へ置き、公開GitHubに含めません。
- 現時点で実YouTube動画でのGemini API稼働・予測精度は未検証です。CIはネットワーク無しの模擬テストです。

公式根拠： [動画理解・公開YouTube URL](https://ai.google.dev/gemini-api/docs/video-understanding)、[Interactions APIの構造化出力](https://ai.google.dev/gemini-api/docs/migrate-to-interactions)、[Gemini APIレート制限](https://ai.google.dev/gemini-api/docs/rate-limits)。

オフラインテスト：

~~~cmd
py -3 -m unittest discover -s tests -p "test_gemini_youtube_multirace.py" -v
py -3 -m unittest discover -s tests -p "test_build_multiview_features.py" -v
~~~
