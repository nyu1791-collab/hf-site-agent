# AI Army Permanent Handoff

**Purpose:** 新しいChatGPTタブ/セッションへ移っても、AI Armyと長編動画制作の重要方針を失わず再開するための恒久引継ぎ入口。  
**Machine manifest:** `config/permanent_standards_manifest.json`

## 新しいタブで最初に行うこと

1. `config/permanent_standards_manifest.json` を読む。
2. Manifestに列挙されたRequired Standardsをすべて読む。
3. 作業開始前に `ai-army/provider-v3` の最新HEADとPR #40の状態を再確認する。
4. Provider/model/free/quotaのLive Evidenceは過去値を使い回さず、必要なら実行直前に再確認する。
5. Main direct push / PR merge / production deploy / publish / secret operation / Durable Object change / paid transitionはHuman Approvalなしで実行しない。

## 変えてはいけない基本方針

### AI Army

- ChatGPT Work / Top Commanderが唯一のRoot。
- Multi-Agentは必要な場合だけ使用し、Single-Agent baselineよりSystem valueが高いことを確認する。
- Worker同士の直接委任や無制限Agent chatは禁止。
- Typed Command / Report contract、Context quarantine、Single Writer、Dependency Join、Trace、Checkpoint、Idempotency、bounded retry/replanを維持する。
- `MAX_DELEGATION_DEPTH=2`、`MAX_PARALLEL_DIRECT_CORPS=3`、`MAX_PARALLEL_SUBORDINATE_WORKERS=1` を基準とする。
- 1 user requestあたり最大10 Task。
- Paid model / paid fallback / auto top-upは禁止。

### 長編動画

最上位Goalは、**再生可能な完成MP4を、正常な中間成果物を壊さず、最小再作業でユーザーへ届けること**。

- Scene / Chapter単位で制作・検証し、最後に結合する。
- 成功Scene、成功VOICEVOX音声、成功Assetは再利用する。
- FFmpeg render中に外部Assetを取りに行かない。
- 音声durationは生成済みWAVをffprobeで実測する。
- 字幕はNarration全体をcoverageする。
- 全SceneをMedia Contractへ正規化し、可能ならconcat copyを使う。
- 同一方法の無限retryは禁止。同じRoot CauseならMethod change / Replanへ進む。
- MP4存在、size>0、ffprobe、video/audio stream、1080x1920、duration範囲を満たすまで完成扱いしない。
- 完成したら長い説明より先にVideo Artifactを提示する。

## Canonical files

- `config/permanent_standards_manifest.json`
- `config/multi_agent_operating_policy.json`
- `docs/MULTI_AGENT_OPERATING_STANDARD.md`
- `docs/AGENT_HIERARCHY.md`
- `config/longform_video_objectives.json`
- `docs/LONGFORM_VIDEO_OBJECTIVES.md`
- `config/longform_video_reliability_policy.json`
- `docs/LONGFORM_VIDEO_RELIABILITY_PLAYBOOK.md`

## Drift prevention

新しいタブで過去会話が十分に見えていなくても、推測で再設計しない。まず上記Canonical filesへ戻る。

次の変更はArchitecture driftとして扱い、明示的な設計レビューなしでは採用しない。

- Unbounded Swarm化
- 全Agentへの全Context broadcast
- Worker間の自由な相互委任
- Free-form chatだけによるTask contract
- Isolated failureで全Video pipelineを最初から再生成
- Long-form monolithic renderの常用
- Free route失敗時のPaid sibling / Paid SaaSへの自動移行
- Machine gate前のCompletion claim

この文書自体は概要であり、競合時はMachine Policyとより具体的なCanonical Standardを優先する。
