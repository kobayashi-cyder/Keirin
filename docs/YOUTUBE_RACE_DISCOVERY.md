# YouTubeから過去の競輪レース映像を探して紐づける

\`tools/youtube_race_search.py\` を使うと、開催日・競輪場・レース番号から **YouTube上のレース映像候補を見つける** ことができます。

- APIキーがなくても、ブラウザで開けるYouTube検索URLを生成します。
- Google Cloudの **YouTube Data API v3** のAPIキーを取得すれば、\`--api-search\` を付けて **公式APIで候補動画を最大15件探せます**。1回の実行につき最大1リクエスト、ページを追って収集しません。
- 動画のタイトル、投稿元チャンネル、公開時刻、URLを画面に出すだけで、検索結果はディスクに保存しません。
- 動画は必ず再生して、**その開催日・競輪場・レース番号が本当に一致することを人が確認**してください。自動検索結果は同一レースを保証しません。
- 確認したYouTube動画IDと対象レースIDを、ローカルのリンク台帳に登録できます。台帳にはタイトル・再生数などのYouTube APIメタデータを保存しません。
- **YouTubeからMP4、音声、字幕、JPEGフレームをダウンロードしません。** YouTubeにリンクすることは映像ファイルの所有権や機械学習権限を意味しません。YouTube上の動画はリンクのみの扱いで、\`video_history.py\` が要求する **利用権限のあるローカル映像ファイル** とは別です。

## 1. Windows：APIキーなしで検索

最新版の \`tools/youtube_race_search.py\` を取得したあと、\`Keirin-main\` で実行：

\`\`\`cmd
py -3 tools\youtube_race_search.py --race-date 2026-11-08 --venue 松山 --race-number 1
\`\`\`

表示された \`browser_search_url\` をブラウザで開いてください。**過去の松山1Rが必ず見つかるわけではありません。** この実行では検索API・OpenAI・Discordへの通信は発生しません。

## 2. YouTube Data APIで検索候補を自動取得

Google Cloudでご自身のプロジェクトに「YouTube Data API v3」を有効化し、同APIが利用できるAPIキーを用意してください。

ローカルの \`.env\` に次の行を追加します（**実際のキーはチャットや公開GitHubへ投稿しないでください**）。

\`\`\`text
YOUTUBE_API_KEY=ここにGoogleCloudのYouTubeDataAPIキー
\`\`\`

OpenAIのAPIキーとは別の認証情報です。 \`.env\` は既存の秘密鍵設定を維持したまま追記してください。

\`\`\`cmd
py -3 tools\youtube_race_search.py --race-date 2026-11-08 --venue 松山 --race-number 1 --api-search
\`\`\`

結果は \`UNVERIFIED_YOUTUBE_VIDEO_CANDIDATES\` として \`video_id\`、\`url\`、\`title\`、\`channel\`、\`published_at\` を返します。公式APIのリクエストは利用量制限の対象です。API応答に失敗しても**APIキーをエラー表示しない**実装です。ヒットしなくても映像が存在しないとは断定できません。

**検索の候補そのものに対する自動的な正解認定・AI映像分析は実装していません。**

## 3. 人の目で確認した1件を、レースIDへリンク

YouTubeで動画を実際に再生し、開催日・競輪場・レース番号が一致していることを確認したときだけ実行します。

\`\`\`cmd
py -3 tools\youtube_race_search.py --race-date 2026-11-08 --venue 松山 --race-number 1 --race-id 7520261108010001 --register-video-id 実際の11文字の動画ID --confirm-reviewed
\`\`\`

このレースIDは**入力書式の例**です。動画IDは必ず検索で見つけた実際のものに置き換えます。

\`local_data\history\youtube_race_links.csv\` に記録されます。同じレースIDを再登録すると上書き拒否します。保存するのは本人が確認した対応情報と動画IDだけで、他チャンネルのAPIメタデータの蓄積はしません。

出力 \`LINK_ONLY_REVIEWED_NOT_TRAINED\` の意味は「**URLとレースの紐づけができただけ**」です。音声・映像を解析したことも、予測モデルが学習したことも意味しません。

## 4. 実際に映像から学習するには

YouTubeのAPIサービス開発者ポリシーは、原則としてYouTubeの事前書面承認なしにAPIサービスを使って映像・音声のコピーをダウンロード・保存することを禁止しています。

YouTubeの公開動画であることやCreative Commons表示だけでは、無条件にAPI経由で動画本体をローカルへ複製・学習できるとは言えません。YouTubeの公開再生リンクを教材管理に使うことと、映像ファイルを機械学習することは別です。

**ご自身がアップロードした映像**なら、YouTube Studioのコンテンツ一覧から公式の「ダウンロード」が可能です。他者の映像は、動画の権利者および必要に応じてYouTubeから、保存と機械学習の使用権限を得た方法で素材を確保してください。

正当に取得できたローカルMP4については、既存の \`tools/video_history.py\` でSHA-256登録・FFmpegによるローカルJPEG抽出が可能です。確認済みの戦術特徴量が揃えば \`tools/build_multiview_features.py\` の20次元モデルに接続できます。

現段階で、動画を自動解釈して競輪の勝率を学習する深層動画モデルはありません。リンク台帳を作っても、過去映像の特徴量が勝手に増えることはありません。

## 5. テスト

\`\`\`cmd
py -3 -m unittest discover -s tests -p "test_youtube_race_search.py" -v
\`\`\`

APIキーを使わず、ネットワークも使わない模擬テストです。

## 公式資料

- [YouTube Data API Search: list](https://developers.google.com/youtube/v3/docs/search/list)
- [YouTube API Services Developer Policies](https://developers.google.com/youtube/terms/developer-policies)
- [YouTube Studioから自分の動画をダウンロード](https://support.google.com/youtube/answer/56100?hl=ja)
