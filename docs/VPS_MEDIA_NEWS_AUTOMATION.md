# VPS 常駐ニュース処理 — 運用・確認手順

更新日: 2026-10-02

## 現在の状態

ユーザー提供のSSH画面では、2026-10-02に systemd のユーザー常駐設定が `Linger=yes`、ニュース処理タイマーが `active` と報告されています。これはログアウト後のユーザーサービス実行とタイマー稼働の証拠ですが、再起動後も有効か、現在のPR版がVPSへ入っているか、RSS取得・DeepSeek呼び出し・VOICEVOX合成・MP4書き出しが成功したかは確認できていません。

この作業対象はユーザー指定の既存VPSです。クラウド事業者や請求残高はこの資料から推定しません。PR #40 は open / draft / unmerged のままにし、main更新、VPSへのデプロイ、秘密情報の変更、動画の公開はしていません。

## 何が5分ごとに動くか

ユーザーサービスのtimerが1回起動するごとに、RSSを一度確認し、処理対象があれば1件だけ進めます。

1. 公式RSSを読み、新しい記事をSQLiteキューへ重複なく記録する
2. 許可した公式記事ページを読み、元記事に根拠がある日本語台本を作る
3. OpenRouter経由の `deepseek/deepseek-v4.1-flash` を台本作成にだけ使う
4. VPS上のVOICEVOXで音声を作り、記事画像候補を保存する
5. 画像の利用根拠・出典・クレジット確認の手前で停止する

ChatGPT APIは使いません。画像・動画生成、外部有料TTS、公開投稿はこの例外に含まれません。素材の権利確認後のレンダリングは明示的な別操作です。人が指示しなくてもtimerは次の起動を繰り返しますが、権利確認待ちの記事が1件ある間は安全のため新しい制作を止めます。

## API費用と停止条件

RSS台本作成用の有料APIは、ユーザーが指定したDeepSeek V4.1 Flashだけです。モデル一覧からその時点の価格とJSON出力対応を確認し、料金上限を満たす場合だけ1回呼びます。

- 1回の推定額: 最大 `0.05 USD`
- UTC日額の予約上限: `0.10 USD`
- UTC月額の予約上限: `0.50 USD`
- UTC日あたり最大: `5回`
- 同じ記事: 有料呼び出しは最大1回
- 料金・モデル・必要機能が確認できない: 呼び出さずキューに残す
- API応答が途切れる、費用が不明、費用が予約額を超える: 自動再試行せず要確認状態にする
- 自動フォールバック、別の有料モデルへの切替、自動トップアップ: なし

月額上限はこのニュース用ルートの予約合計を制限します。他のAPI利用やアカウント全体の残高を照会・制御するものではありません。別のAPI利用で共有残高を使う場合は、アカウント全体の残額を別途確認してください。キーはチャットやログに貼らず、VPS上の保護された環境ファイルで管理します。

## SSHで状態を一度に確認

VPSのSSHシェルで実行する読み取り確認です。秘密鍵、APIキー、記事本文は表示しません。

```bash
loginctl show-user "$USER" -p Linger
systemctl --user is-active hf-site-agent-media-news.timer
systemctl --user is-enabled hf-site-agent-media-news.timer
systemctl --user show hf-site-agent-media-news.timer -p ActiveState -p UnitFileState -p LastTriggerUSec -p NextElapseUSecRealtime
systemctl --user show hf-site-agent-media-news.service -p Result -p ExecMainStatus -p ActiveState
df -h "$HOME"
python3 -m scripts.media_source_ingress --db "$HOME/hf-site-agent/runtime/media-queue.sqlite3" status
```

確認の意味:

- `Linger=yes`: ログアウト中もユーザーのsystemdを動かす設定
- timerの `active`: いまタイマーが起動中
- timerの `enabled`: ログイン/再起動後にtimerを戻す設定。これがないと再起動後の継続は未確認
- serviceの `Result=success` と `ExecMainStatus=0`: 直近の1回が正常終了した目安
- キューの `IDLE`: 現在処理待ちなし。timer自体の停止を意味しない
- 空き容量が2 GiB未満: 新しいAPI・音声処理を開始せず、ファイルも自動削除しない

timerが `not-found` または `disabled` なら、このPRはまだVPSへ導入されていません。状態を記録してから、既存VPSのサービス定義と保護環境を照合してください。請求設定や秘密情報を変更せずに確認できるところまでが先です。

## 完成と判定するための証拠

| 到達点 | 必要な証拠 |
| --- | --- |
| 常駐 | `Linger=yes`、timer `active` と `enabled`、次回実行時刻 |
| RSS | キューの取得時刻が更新され、新規記事が重複なく入る |
| DeepSeek | 台本チェックポイントに正確なモデルID、推定額・実費、同じ記事への二重呼び出しなし |
| 音声 | VOICEVOXの実音声ファイルと時間情報 |
| 動画 | 権利確認済み素材を使ったMP4が非空で、ffprobeで映像・音声streamを確認 |
| 公開 | 現時点では無効。投稿先と範囲の別承認が必要 |

この資料とCIが通っても、VPSの現行稼働や完成MP4を証明したことにはなりません。VPSにはPRのコードを安全に導入できる状態になってから、上の確認をまとめて実施します。

## 参照

- [GitHub Actions Runnerのセルフホスト手順](https://docs.github.com/actions/hosting-your-own-runners/managing-self-hosted-runners/adding-self-hosted-runners)
- [OpenRouter DeepSeek V4.1 Flashのモデル情報](https://openrouter.ai/deepseek/deepseek-v4.1-flash)
- [常駐処理ポリシー](../config/media_news_pipeline_policy.json)
- [systemd user service](../deploy/systemd/user/hf-site-agent-media-news.service)
- [systemd user timer](../deploy/systemd/user/hf-site-agent-media-news.timer)
