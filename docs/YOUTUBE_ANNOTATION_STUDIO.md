# YouTube映像の戦術を、人が視聴しながら記録する

新しいツール：tools/youtube_annotation_studio.py と tools/youtube_annotation_studio.html。

**公式YouTube埋め込みプレーヤーで過去レースを再生しながら、「仕掛け」「先頭区間」「終盤の追い上げ」を人の手で時刻付き記録します。動画のダウンロード・音声抽出・フレーム保存・自動認識は行いません。**

## 1. Windowsで最新のファイルを取得

プロジェクトの Keirin-main でCMDを開き、4ファイルを更新します。

- tools/youtube_annotation_studio.py
- tools/youtube_annotation_studio.html
- tools/build_multiview_features.py
- tools/youtube_race_search.py

取得元は各ファイルの https://raw.githubusercontent.com/kobayashi-cyder/Keirin/main/tools/ 以下です。

## 2. YouTubeからレースを探し、レースIDにリンク

開催日の例（動画や競走結果の実在は別途確認）：

~~~cmd
py -3 tools\youtube_race_search.py --race-date 2026-11-08 --venue 松山 --race-number 1
~~~

YouTubeでレースの開催日・場所・R番号を実際に視聴して確認してください。一致した動画の watch?v= 以降の**11文字のID**を以下に指定します。

~~~cmd
py -3 tools\youtube_race_search.py --race-date 2026-11-08 --venue 松山 --race-number 1 --race-id 7520261108010001 --register-video-id 実在の動画ID11文字 --confirm-reviewed
~~~

リンク登録が済んでいなければ注釈画面は起動しません。検索結果からレースIDが自動的に確定するわけではありません。

## 3. 再生・戦術注釈画面を起動

以下の発走時刻・レースグレードは**入力書式の例**です。実際のレース公式結果と照合した値に置き換えてください。

~~~cmd
py -3 tools\youtube_annotation_studio.py --race-id 7520261108010001 --race-at-jst "2026-11-08T20:40:00+09:00" --venue-key matsuyama --grade F2 --cars 7
~~~

ブラウザに http://127.0.0.1:8765/ が開きます。開かなければCMD画面に表示されるURLへアクセスしてください。ローカルサーバーを終了するには Ctrl+C。

- YouTube IFrame Player APIで公式プレーヤーを表示します。YouTube側の通常再生・広告・操作系には干渉しません。
- 車番を選び、仕掛け時は「仕掛けを記録」、先頭開始と終了で先頭秒数を記録、終盤で順位を上げたときに順位数とともに「終盤の追い上げ」を押します。
- 各選手の正しい rider_id（異なるレースで同じ選手を指す一意ID）、確定着順、ライン連携スコア0～1を入力します。画面で全員分の確認にチェックを入れて初めて保存可能です。
- 同着・欠場・返還などの複雑な着順はこの画面では扱えません。無理に順位を創作せず注釈を中止してください。
- 埋め込み不可のYouTube動画は外部YouTubeリンクで視聴できますが、ローカル画面での現在時刻の自動取得ができないためマーカーは付けられません。再生制限を回避しません。

「観察記録をローカルCSVへ保存」を押すと local_data/history/youtube_rider_events.csv に全選手の行が保存されます。二重保存・上書きを拒否します。

**保存時刻はサーバー側で付けた本当の available_at_jst です。** 今視聴した動画を、過去のレース前にすでに観察していたようには記録できません。

## 4. 20次元の着順予測学習へ結合する

過去のYouTube動画について、保存するのは**視聴した人自身が作った戦術メモと再生位置**です。映像ファイルやAPIの動画メタデータではありません。記録には youtube_video_id と source_url を含め、実際のレースIDに紐づく確認済み動画であることを再照合します。

予測対象レースの出走前CSV local_data/pre_race_features.csv と、選手IDを一致させた local_data/history/target_context.csv が必要です。その上で：

~~~cmd
py -3 tools\build_multiview_features.py --events local_data\history\youtube_rider_events.csv
~~~

MULTIVIEW_DRY_RUN_VALID が得られれば、末尾に --commit を付けて local_data/pre_race_multiview.csv に保存します。実データの出走前取得時刻に遅れた注釈は除外されます。

500レース以上・30日以上の既存学習ゲートを満たしたとき、以下の研究用学習が可能になります。

~~~cmd
py -3 tools\train_baseline.py --features local_data\pre_race_multiview.csv --results exports\results_3rentan.csv --output local_data\models\research_multiview.json --multiview
~~~

バックテストにはさらに、締切前オッズ・その時点のモデル確率・実際の確定払戻が必要です。新しい注釈1レースだけではモデル学習も利益検証も完了しません。

既存のローカル映像用 rider_events.csv とは分離して保存するため、両方を同時に学習したい場合は、ID重複や時刻を検証した統合CSVを作る必要があります。

## 5. セキュリティとデータの取り扱い

- ローカルWebサーバーは 127.0.0.1 のみで待ち受け、保存時はセッション用トークンと同一オリジンを検証します。
- 注釈データを公開GitHub、OpenAI、Discordへ送信しません。YouTubeの通常プレーヤー通信だけYouTubeへ発生します。
- rights_status=self_recorded は自分が観察して作ったメモに付けるもので、YouTube動画の複製権限を意味しません。
- 仕掛けやライン連携の判断には誤記録・主観的なばらつきがあります。予測モデルに入れたら必ず時系列分割した実績検証が必要です。

公式参照： https://developers.google.com/youtube/iframe_api_reference および https://developers.google.com/youtube/terms/developer-policies

オフラインテスト：

~~~cmd
py -3 -m unittest discover -s tests -p "test_youtube_annotation_studio.py" -v
py -3 -m unittest discover -s tests -p "test_build_multiview_features.py" -v
~~~
