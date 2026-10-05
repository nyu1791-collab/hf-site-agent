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

2026-10-05実機結果：修復Run37272663789は07:32 UTCに成功。write_verify Run37279064554は07:40 UTCに成功し、独立Runnerがファイル作成/read-backのPASSを返した。両方の実操作経路を確認済み。外部IAP SSHは再起動前後にtimeout/4003があり、成功未確認。status / write_verify / connectivity_repair / memory_guardは同じ保守ブランチから選択できる。

Cloud Shellの新しいターミナル（名前@cloudshell:~$）で実行する接続確認は次の1行。末尾までコピーすると同じ行への二重貼付はコメント化される。別行でも接続確認は安全に再実行できる。

```bash
gcloud compute ssh n_yu1791@instance-20261001-071545 --project=project-fdadb4dd-cb77-4270-9cd --zone=us-central1-a --tunnel-through-iap --ssh-flag="-o ConnectTimeout=20" --command='id; echo SSH_OK'; # END
```

既存資格情報・SSHホスト検証を維持する。Gemini IAM/ADC再設定、認証解除、公開管理ポート追加、新VM作成は不要。接続不良だけでGemini/VOICEVOX/Rendererの故障と判断しない。

## 修復と保守

scripts/repair_vm_connectivity.pyは対象VM・既存unit各1個・user bus・必要なsudo権限を確認する。checkoutのreset/switch、制作ジョブ停止、秘密情報出力を行わない。healthyサービスは再起動しない。activeのままクラウド接続が固まる状態はこの新watchdogでは判定しないため、クラウド側Onlineと実コマンドも確認する。

保守停止は~/.config/hf-site-agent/connectivity.pauseで明示する。これを設けた停止はwatchdogで復活させない。

## メモリ・実行分離

実機は約2GB RAM。swapは0から2GBへ追加済み。以前のserial logにUnder memory pressure反復がある。再起動後07:30 UTCのavailable約1.3GBは確認したが、長期安定や動画負荷中の余裕を証明しない。

memory_guard Run37279409409は2026-10-05 07:44 UTCに成功。/hf-connectivity.swapはroot所有・600・2147483648 bytes、active swap約2GBを別の直接操作でも確認。/etc/fstabに対象の1行を追加して再起動時も有効化する。再適用時は再フォーマット・重複行追加をしない。Desktop Commanderのsudo制限を隠す方法は使わず、検証済みprivate Runnerの正規操作で適用した。小型VMは接続・queue・監視を優先し、最終レンダーは既存GitHub-hosted fallbackを利用する。swapは一時的メモリ圧への対策であり、長期安定や容量増設の代わりを証明しない。

動画制作をチャットforegroundへ依存させない。固有job_idとSTATE/checkpointを持つ耐久実行に渡し、切断復帰でJobを二重登録しない。全工程のdurable接続が未実装なら実装済みとは報告しない。

## 担当と制作ルール

ユーザー指定によりコード周りはCodex 6.1 Solに担当させ、Commanderが運用・事実・設定・完了条件を統合確認する。コードの複数Writerや重複AI査読は増やさない。

新動画はdocs/VIDEO_FRESH_CONTENT_POLICY_20261005.mdに従い、土台・型・Renderer・キャラ素材を再利用する。情報収集・Gemini解析・統合・台本・音声は動画ごとに新規作成。content_run_idごとに解析/WAV cacheを分離し、同じJobの復旧時のみ今回の成功checkpointを使用する。過去のMP4・研究Package・WAVは保持する。

接続基盤の復旧を先に完了し、その後にGemini4の新動画を制作する。制作本体約5分は実測目標。main変更、PR #40 merge、公開投稿は禁止。


## VOICEVOX常駐・コード反映

2026-10-05 07:51 UTCにscripts/install_resident_voicevox.pyを別worktreeから実行し、VOICEVOX_RESIDENT_READYを確認。voicevox.serviceはenabled / active、Restart=always、RestartSec=3、CPU1スレッド、127.0.0.1:50021限定。/versionは0.25.2を返した。健康なサービスは再適用しても再起動せず、管理外の健康Engine/既存の異なるunitを勝手に停止しない。

設定コードは7b3efb5ddbaa25355486dc89a33ca168c549b756、実機worktreeは/home/n_yu1791/hf-site-agent-runs/setup-20261005-fixed/code。既存/home/n_yu1791/hf-site-agentの未コミット変更・成果物は保持し、reset/checkoutで上書きしていない。

動画ビルダーの子Python3箇所はsys.executableへ変更、content_run_id必須・同Job解析音声cache・480〜720秒のRender前尺検査・中立MP4名・入力SHA一致時だけ完成checkpoint再利用を実装した。Renderer本体は変更していない。これらはコード検証済みで、今回の新動画のMP4完成を意味しない。

## 耐久研究ジョブの実機確認

2026-10-05 08:11 UTC、scripts/durable_gemini_research.py（16b8f29e）で新content_run_id gemini4-argon-fresh-20261005-0807を登録し、user systemdのhf-research-gemini4-argon-fresh-20261005-0807.serviceをenable/start。08:15 UTCにRESEARCH_READY、source_count=3、cache_hits=0、synthesis_cache_hits=0、successful_requests=4を確認した。解析3本と統合1回は今回新規実行で、Geminiの接続試験ではない。

STATEと成功cacheは~/.local/state/hf-site-agent/video-jobs/<content_run_id>/に保存。flockと入力SHAで同Jobを識別し、応答結果不明はUNKNOWN_RESULTで止め、自動再送しない。研究workerはMP4完成扱いにせずRESEARCH_READYで停止する。新台本を別の外部Runnerに渡す後続工程はCommanderが行うため、現状は全工程の自動連結までは未完了。

新制作の外部経路はtransient branch run/gemini4-argon-fresh-20261005-0807のGitHub-hosted Actions。既存Renderer・キャラ型だけ再利用し、WAVは新規合成。端末が閉じても登録済みVM研究とActionsの実行は端末のforeground接続を必要としない。一般的なクラウド障害やVM停止を含む常時オンライン保証ではない。
