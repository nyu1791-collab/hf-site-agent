# VM接続復旧（2026-10-05）

対象: 既存 instance-20261001-071545 のみ。Desktop CommanderのOfflineはVM停止や動画コード障害の証明ではない。

## 独立した操作経路

1. Desktop Commander：通常操作。
2. private GitHub self-hosted Runner：Desktop Commanderに依存しない既存操作経路。nyu1791-collab/-hf-vm-control の VM control を使う。
3. Google Cloud Console/Cloud Shell SSH：上記両方が停止した際の外部からの復旧経路。既存VMへgcloud compute sshで接続する。新VM、公開管理ポート、認証解除、Gemini IAM/ADCの変更は不要。

VMそのものが停止・ネットワーク断の場合、VM内部のサービスだけでは復旧できない。Cloud Shell経路の認証と接続も実機確認が必要。

## 用意済みの復旧操作

private control repository の maintenance/connectivity-recovery-20261005 ブランチ。既存 vm-control.yml をRun workflowし、同ブランチと connectivity_repair を選択する。mainは変更しない。

この操作は正本修復スクリプトの固定コミット4791fabcf8d3c018b270f1eb5d5670b4e6cfef26を取得して実行する。VMのcheckoutはreset/switch/updateしない。Runnerの稼働中プロセスを再起動せず、Desktop Commanderだけ初回に1回再起動する。

scripts/repair_vm_connectivity.py は、既存のDesktop Commander systemdサービスとprivate Runnerサービスがそれぞれ1個であること、対象VM、user systemd bus、必要なsudo権限を確認する。サービスが見つからない/曖昧な場合はBLOCKEDとし、認証情報やExecStartを出力しない。既存の起動コマンド・資格情報を変更しない。

追加内容: サービスdrop-inのRestart=always / RestartSec=30 / StartLimitIntervalSec=0、linger、hf-connectivity-watchdog.timerによる毎分のinactiveサービス復旧。保守停止は ~/.config/hf-site-agent/connectivity.pause で明示する。activeのままネットワーク接続が固まった状態は自動判定しない。ローカルactiveとクラウド側onlineは区別する。

## 完了条件

復旧workflow成功だけでは完了にしない。Desktop Commander list_devicesでOnline、実コマンド成功、private Runnerによる別status操作成功、サービスenabled/active・timer enabled/active・linger=yesを確認する。Cloud Shell SSHは両サービス停止時にも使える第三経路として別途検証する。既存キュー・制作中ジョブ・MP4を保持する。

## 制作開始の順序

接続基盤を優先し、復旧確認前に新動画を開始しない。動画はチャットのforeground processではなく既存のdurable Job/systemd実行へ渡す。STATE/checkpointから復帰し、接続切断でJobを重複登録しない。新動画の解析・台本・音声は VIDEO_FRESH_CONTENT_POLICY_20261005.md に従って一から作成する。

## 現在の検証範囲

ローカルでPython compilationと6つの対象チェック成功。VM適用・オンライン復帰・再起動後復旧・SSH第三経路は未検証。設定コードを保存しただけで稼働済みとは報告しない。


## 2026-10-05 実操作状況と外部復帰コマンド

復旧Run 37272663789 を実際にworkflow_dispatchした。GitHubは Waiting for a runner to pick up this job... と表示。VM適用未開始。Google Cloud Consoleはこの操作ブラウザでSite Unavailableとなり、外部SSH未実行。成功とは扱わない。

既存Runnerを外部の認証済みCloud Shellから起動する:

```bash
task_zone="$(gcloud compute instances list --project=project-fdadb4dd-cb77-4270-9cd --filter='name=instance-20261001-071545' --format='value(zone.basename())')"
if [ -n "$task_zone" ] && [ "$(printf '%s\n' "$task_zone" | wc -l)" -eq 1 ]; then
  gcloud compute ssh n_yu1791@instance-20261001-071545 --project=project-fdadb4dd-cb77-4270-9cd --zone="$task_zone" --command='sudo -n systemctl start actions.runner.nyu1791-collab--hf-vm-control.hf-site-agent-vm.service'
else
  printf '%s\n' '対象VMのzoneを一意に取得できませんでした。'
fi
```

停止した既存Runnerをstartするだけで、稼働中なら再起動しない。IAM/ADC・秘密情報・VM電源・main・制作データは変更しない。Runner復帰後は待機中のRunが実行される。Runが期限切れなら同じmaintenance branchのconnectivity_repairを再dispatchする。SSH失敗は権限・到達性・ホスト鍵を調べ、検証解除で回避しない。
