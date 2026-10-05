# VM常駐・接続復旧（2026-10-05）

対象は既存VM instance-20261001-071545、project-fdadb4dd-cb77-4270-9cd、us-central1-a。PC・iPad・Cloud Shell・チャットは操作画面で、常駐処理はVM上で動かす。端末を閉じてもsystemdサービスは継続する。VM自体の停止・ネットワーク障害・クラウド障害までオンラインを保証しない。

## 実機適用済み

2026-10-05 07:23 UTC以降、Desktop Commanderから実コマンド・ファイル作成・read-backが成功。接続端末はf9f63e6e-ba59-406b-a345-631d3fe19625、Linuxユーザーn_yu1791（uid1000）。

- desktop-commander-remote.service：user unit、enabled / active、Restart=always、RestartSec=30。
- actions.runner.nyu1791-collab--hf-vm-control.hf-site-agent-vm.service：system unit、enabled / active、Restart=always、RestartSec=30。
- Linger=yes：ユーザーlogout後もuser managerを維持。
- hf-connectivity-watchdog.timer：enabled / active、毎分inactiveサービス復旧。AccuracySec=1s、RandomizedDelaySec=0。
- journalでSERVICES_ACTIVE / failed_units=[]の反復を確認。
- ~/.local/state/hf-site-agent/connectivity/write-verification.jsonに直接write/read-back成功。

実際に端末を閉じた前後の継続試験は未実施。ユーザーが実行したVM reset後にRunnerの起動・GitHub接続・Listening for JobsとDesktop Commander復帰を確認したが、今回追加した全drop-inの再起動後検証は別チェック。未検証を完了と報告しない。

## 独立した操作経路

1. Desktop Commander：通常のshell・ファイルwrite経路。
2. private repository nyu1791-collab/-hf-vm-controlの既存self-hosted Runner：Desktop Commander非依存の操作経路。maintenance/connectivity-recovery-20261005のVM controlを使用。mainは変更しない。
3. 認証済みGoogle Cloud ShellからIAP SSH：両サービスが使えない場合の外部復旧経路。
4. SSH不通時はCompute APIで起動・serial log確認。serial consoleは対象VMで有効化済みだが、接続成功とOSログイン成功は別。login:にはgcloudコマンドを貼らない。

2026-10-05 07:35 UTC時点：GitHub Runner画面はIdle、ラベルself-hosted/Linux/X64。旧修復Run37272663789はqueuedのまま。ローカルactive・GitHub Idleだけでは独立操作成功とは扱わない。外部IAP SSHは再起動前後にtimeout/4003があり、成功未確認。

Cloud Shellの新しいターミナル（名前@cloudshell:~$）で実行する接続確認は次の1行。末尾までコピーすると同じ行への二重貼付はコメント化される。別行でも接続確認は安全に再実行できる。

```bash
gcloud compute ssh n_yu1791@instance-20261001-071545 --project=project-fdadb4dd-cb77-4270-9cd --zone=us-central1-a --tunnel-through-iap --ssh-flag="-o ConnectTimeout=20" --command='id; echo SSH_OK'; # END
```

既存資格情報・SSHホスト検証を維持する。Gemini IAM/ADC再設定、認証解除、公開管理ポート追加、新VM作成は不要。接続不良だけでGemini/VOICEVOX/Rendererの故障と判断しない。

## 修復と保守

scripts/repair_vm_connectivity.pyは対象VM・既存unit各1個・user bus・必要なsudo権限を確認する。checkoutのreset/switch、制作ジョブ停止、秘密情報出力を行わない。healthyサービスは再起動しない。activeのままクラウド接続が固まる状態はこの新watchdogでは判定しないため、クラウド側Onlineと実コマンドも確認する。

保守停止は~/.config/hf-site-agent/connectivity.pauseで明示する。これを設けた停止はwatchdogで復活させない。

## メモリ・実行分離

実機は約2GB RAM、swap 0。以前のserial logにUnder memory pressure反復がある。再起動後07:30 UTCのavailable約1.3GBは確認したが、長期安定や動画負荷中の余裕を証明しない。

swap追加は未適用。Desktop CommanderのsudoコマンドがCommand not allowedで拒否されたため、コマンドを隠して回避しない。正規のCloud Shell SSHまたは検証済みprivate Runner経路で適用・再確認する。小型VMは接続・queue・監視を優先し、最終レンダーは既存GitHub-hosted fallbackを利用する。

動画制作をチャットforegroundへ依存させない。固有job_idとSTATE/checkpointを持つ耐久実行に渡し、切断復帰でJobを二重登録しない。全工程のdurable接続が未実装なら実装済みとは報告しない。

## 担当と制作ルール

ユーザー指定によりコード周りはCodex 6.1 Solに担当させ、Commanderが運用・事実・設定・完了条件を統合確認する。コードの複数Writerや重複AI査読は増やさない。

新動画はdocs/VIDEO_FRESH_CONTENT_POLICY_20261005.mdに従い、土台・型・Renderer・キャラ素材を再利用する。情報収集・Gemini解析・統合・台本・音声は動画ごとに新規作成。content_run_idごとに解析/WAV cacheを分離し、同じJobの復旧時のみ今回の成功checkpointを使用する。過去のMP4・研究Package・WAVは保持する。

接続基盤の復旧を先に完了し、その後にGemini4の新動画を制作する。制作本体約5分は実測目標。main変更、PR #40 merge、公開投稿は禁止。
