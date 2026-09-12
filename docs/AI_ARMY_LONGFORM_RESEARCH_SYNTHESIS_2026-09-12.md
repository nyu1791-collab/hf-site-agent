# AI Army / 長編動画制作 Research Synthesis

**Status:** Canonical research synthesis  
**Effective:** 2026-09-12 JST  
**Purpose:** 公開一次資料・研究論文・実運用OSSを横断し、AI Armyと長編動画パイプラインに何を採用し、何を標準経路から外すかを明示する。

この文書は「知識を増やす」こと自体を目的にしない。採用条件は、**再現性・障害分離・再開性・総合効率・無料運用との整合・機械検証可能性**である。

---

## 1. Evidence hierarchy

設計判断は次の順で重く扱う。

1. 公式仕様・公式Engineering文書
2. 査読論文・主要研究機関のTechnical Report
3. 現行AI Armyで再現できるテスト、Trace、失敗事実
4. 成熟OSSのコード・Issue・運用事例
5. コミュニティ経験談
6. Agentの提案・多数決

AI同士の一致は根拠を補助するが、公式仕様や機械検証を上回らない。

---

# Part A — Multi-Agent / 組織AI

## 2. 調査した主要情報源

### Anthropic Engineering

- How we built our multi-agent research system  
  https://www.anthropic.com/engineering/multi-agent-research-system
- Effective harnesses for long-running agents  
  https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Harness design for long-running application development  
  https://www.anthropic.com/engineering/harness-design-long-running-apps
- Building a C compiler with a team of parallel Claudes  
  https://www.anthropic.com/engineering/building-c-compiler
- Writing effective tools for AI agents  
  https://www.anthropic.com/engineering/writing-tools-for-agents
- How we contain Claude across products  
  https://www.anthropic.com/engineering/how-we-contain-claude

### OpenAI Agents SDK

- Agents SDK overview  
  https://openai.github.io/openai-agents-python/
- Agent orchestration  
  https://openai.github.io/openai-agents-python/multi_agent/
- Guardrails  
  https://openai.github.io/openai-agents-python/guardrails/
- Handoffs  
  https://openai.github.io/openai-agents-python/handoffs/
- Tracing  
  https://openai.github.io/openai-agents-python/tracing/

### LangChain / LangGraph

- Multi-agent patterns  
  https://docs.langchain.com/oss/python/langchain/multi-agent
- Subagents / supervisor pattern  
  https://docs.langchain.com/oss/python/langchain/multi-agent/subagents

### Microsoft AutoGen / Research

- AutoGen termination conditions  
  https://microsoft.github.io/autogen/dev/user-guide/agentchat-user-guide/tutorial/termination.html
- AutoGen teams  
  https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/teams.html
- Magentic-One  
  https://www.microsoft.com/en-us/research/articles/magentic-one-a-generalist-multi-agent-system-for-solving-complex-tasks/

### Google / Linux Foundation A2A

- A2A Protocol  
  https://a2a-protocol.org/

### Academic literature

- MetaGPT, ICLR 2024 — SOPを明示してnaive chainingによるcascading hallucinationを抑える設計  
  https://proceedings.iclr.cc/paper_files/paper/2024/hash/6507b115562bb0a305f1958ccc87355a-Abstract-Conference.html
- ChatDev, ACL 2024 — 役割別Agentと構造化されたcommunicative chain  
  https://aclanthology.org/2024.acl-long.810/
- MultiAgentBench, ACL 2025 — topologyやcoordination strategyをtask completionだけでなくmilestoneで評価  
  https://aclanthology.org/2025.acl-long.421/
- Can LLM Agents Really Debate?, 2025 — debateではagent diversityと基礎reasoning strengthが重要で、majority pressureが独立修正を阻害しうる  
  https://arxiv.org/abs/2511.07784
- Towards a Science of Scaling Agent Systems, 2025 — task特性によってmulti-agent overheadやerror amplificationが増え、sequential reasoningでは悪化し得るという実証  
  https://arxiv.org/abs/2512.08296

---

## 3. 採用するMulti-Agent原則

### A. Central managerを標準とする

Top Commanderが最終責任、routing、budget、acceptance criteria、統合を保持する。Specialistはtool/subagentとして限定Scopeで動く。

**理由:** OpenAI Agents SDK、LangChain subagents、Magentic-One、Anthropic Researchの実装方向が一致する。現在のAI Armyの階層型構造とも整合する。

### B. Single Agent baselineを先に測る

Multi-Agentは常時起動しない。単一Agent + 適切なtoolで十分ならそれを使う。

**採用Gate:** distinct expertise / independent parallel work / independent verification / context isolation / long-running checkpointed delegation の少なくとも1つが必要。

### C. Task lease / ownership lockを追加する

並列Agentは同じTaskや同じmutable outputを同時に奪い合わない。Task claimにはlease owner、lease expiry、idempotency key、payload hashを持つ。

**根拠:** Anthropicのparallel compiler experimentでは、複数Agentが同じ問題へ集中すると上書き・重複作業が起き、task lockingとknown-good oracleによる分割が必要だった。

### D. Sessionを跨ぐ状態はArtifactへ出す

会話Contextを長く持ち続けるのではなく、Mission state / artifact / checkpoint / handoff briefへ構造化して保存する。新しいAgent sessionはそのArtifactから再開する。

**根拠:** Anthropicのlong-running harnessはinitializer + incremental agent + handoff artifactsを重視。長いContextだけに依存しない。

### E. Generator / Evaluatorを分けるが、無限Reflectionにしない

高影響出力はPrimary + Independent Verifierを標準とする。ReviewerはAcceptance Criteria、Evidence、Artifactを読み、PASS / REVISION / BLOCKEDを返す。

同じエラーを2回以上繰り返す場合は、同じpromptで再試行せずRoot Cause / Replanへ移る。

### F. Tool設計を整理する

Agentへ数百の似たtoolを見せない。Toolは明確な責務でnamespaceし、必要時だけloadする。返り値は全文dumpよりhigh-signal summary + artifact referenceを優先する。

**根拠:** Anthropicはtool overlapと巨大なtool outputsがAgentのcontextを圧迫し、tool selectionを悪化させると報告している。

### G. Side-effect guardrailは実行前にblockingで行う

Payment、publish、merge、deploy、secret、production writeなどは、Agent-level guardrailだけに依存しない。実際のtool直前でauthorization / policy / budgetを同期的に確認する。

**根拠:** OpenAI Agents SDKはparallel guardrailではAgent/toolが先に動き始める可能性を明記している。不可逆操作ではpre-execution blockingを採用する。

### H. 明示的Terminationを複合する

Agent loopには最低でも以下から複数の停止条件を持たせる。

- max turns / messages
- max requests
- token budget
- wall-clock timeout
- task completion
- blocked/human approval
- repeated-root-cause circuit breaker

AutoGenがtermination conditionを第一級概念として扱うのと同じく、終了条件なしのteamは認めない。

### I. Evaluatorはknown-good oracleを使う

可能なTaskは既知の正解、Schema、fixture、test suite、compiler、ffprobe、hash、snapshotなど機械Oracleで評価する。Agent reviewerの文章だけでPASSにしない。

### J. Securityはpermission promptよりblast-radius制限を優先する

Agentに「危険な操作をしないで」と頼むだけではなく、そもそもWorker credential / egress / tool permissionを制限する。Human approval fatigueも考慮し、危険境界だけで明確に停止する。

---

## 4. Multi-Agentで標準経路から外すもの

### REMOVE / DOWNGRADE 1 — 常時Debate

常時debateは採用しない。論理課題などで明確な価値があり、独立初期回答を保持し、round limitを設定した場合だけ実験扱い。

**理由:** debate研究では多数派圧力が誤ったconsensusを強化する場合がある。多数決はground truthの代替ではない。

### REMOVE / DOWNGRADE 2 — 全Framework同時運転

LangGraph / CrewAI / AutoGen / Copilot系を同じMissionで同時に重ねない。

- Native runtime = source of truth
- LangGraph = stateful graph/checkpointが必要な場合の第一Adapter
- CrewAI = 明確な短期専門Crewでのみon-demand
- AutoGen = bounded council / dialogue experimentのみ
- Copilot Agent = repository codingの限定adapterのみ

Framework数を増やすことはKPIにしない。

### REMOVE / DOWNGRADE 3 — A2Aを内部通信の必須規格にしない

A2Aは異なるremote agent/vendor間の境界でのみ検討する。内部のlocal agent delegationは既存Command/Report Envelopeで十分。

### REMOVE / DOWNGRADE 4 — すべてのAgentへ全Contextを配る

禁止。Subagentには必要部分だけを渡す。大量tool outputはartifactへ逃がす。

### REMOVE / DOWNGRADE 5 — 同じ質問を複数Agentへ投げるだけの多数決

原則禁止。複数Agentを使うなら役割、Evidence source、failure modeを分離する。Independent verifierはPrimaryの結論を先に見ないblind-first modeを選べるようにする。

---

# Part B — 長編AI動画

## 5. 調査した主要情報源

### FFmpeg / ffprobe公式

- FFmpeg Formats / concat demuxer  
  https://ffmpeg.org/ffmpeg-formats.html
- FFmpeg Filters / loudnorm  
  https://ffmpeg.org/ffmpeg-filters.html
- ffprobe  
  https://ffmpeg.org/ffprobe.html

### VOICEVOX

- VOICEVOX ENGINE  
  https://github.com/VOICEVOX/voicevox_engine

### GitHub Actions

- Dependency caching  
  https://docs.github.com/en/actions/concepts/workflows-and-actions/dependency-caching

### AI動画OSS / issue evidence

- MoneyPrinterTurbo  
  https://github.com/harry0703/MoneyPrinterTurbo
- MoneyPrinterTurbo audio-duration failure example  
  https://github.com/harry0703/MoneyPrinterTurbo/issues/158
- MoneyPrinterTurbo subtitle/provider coupling failure example  
  https://github.com/harry0703/MoneyPrinterTurbo/issues/1089
- ShortGPT  
  https://github.com/RayVentura/ShortGPT

---

## 6. 採用する長編動画原則

### A. Audio-first timeline

台本文字数ではなく、生成済みVOICEVOX WAVの実測durationをtimelineの正本にする。字幕blockも音声blockと1:1で追跡する。

### B. VOICEVOX rawとnormalized audioを分ける

VOICEVOXの標準出力は24kHzになり得る。生成直後のraw WAVを保存し、video media contract用に48kHz stereoへ正規化したderivativeを別hashで保存する。

- raw voice = 再合成回避用
- normalized voice = FFmpeg scene入力用

speaker UUID、style ID、engine version/digest、AudioQuery主要parameterをmanifestへ保存する。

### C. Scene = atomic build unit

Sceneを `.partial.mp4` に生成し、process exit、ffprobe、media contract、duration、subtitle coverageを通した後だけ正式Sceneへatomic renameする。

後半Scene失敗で前半Sceneを再生成しない。

### D. ffprobeだけでなくdecode smoke testを持つ

ffprobeはcontainer/stream metadata検査に強いが、最終成果物の全packet decode異常を完全に代替しない。final候補は可能なら `ffmpeg -v error -xerror -i final.mp4 -f null -` 相当のdecode smokeを行い、長大すぎる場合はsampling decode + ffprobeを最低線とする。

### E. concat inputを安全なmanifestに限定する

concat listは生成コードが作るrelative sanitized pathだけを使い、外部protocolや任意path injectionを許可しない。media contract一致時のみstream-copy concatを選ぶ。

### F. CacheとArtifactを分ける

GitHub Actions公式が区別する通り、cacheは「なくても再生成できる高速化用」、artifact/checkpointは「run間で保持すべき検証済み成果物」に使う。

**禁止:** GitHub cache hitだけをdurable checkpoint成功とみなすこと。

### G. File descriptor / subprocess hygiene

長編は画像・音声・FFmpeg processを多数扱うため、file handle、pipe、subprocessを必ずclose/waitする。MoneyPrinterTurboでも`Too many open files`が運用上の既知問題になっている。

### H. Subtitle生成をTTS provider内部状態へ結合しない

字幕のsource of truthはNarration Manifest。特定TTSの内部objectが無いだけで字幕工程をskipしない。MoneyPrinterTurboのissueでもprovider-specific guardがsubtitleを誤停止させた事例がある。

### I. Loudnessは計測してから正規化する

FFmpegの`loudnorm`はEBU R128を実装している。固定のインターネット俗説値を無条件適用せず、まずmeasureし、fixture/user評価で採用したtargetへ必要な場合だけ2-pass normalizationする。

### J. Preview/proxyは高コストSceneだけに限定

すべてを二重renderしない。重いScene・複雑なlayout・新しいfilter graphだけ低解像度proxyを先に作って構造検査し、既知の安定Sceneは直接final contractへ進める。

### K. Asset provenance ledger

各画像/動画/音声についてsource、取得時刻、content hash、decode結果、rights/status、normalized derivative hashを記録する。外部URLそのものをrender時入力にしない。

---

## 7. 長編動画で標準経路から外すもの

### REMOVE / DOWNGRADE 1 — 全SceneでWhisper再文字起こし

ずんだもんTTSでは元台本が既知なので、全SceneへASRをかけるのは標準にしない。ASRはQA spot-checkまたは外部音声のみに使う。

### REMOVE / DOWNGRADE 2 — 全Sceneのproxy二重render

標準禁止。複雑Sceneのみ。単純Sceneでは時間とstorageの無駄が増える。

### REMOVE / DOWNGRADE 3 — URL-only cache key

禁止。content bytes hash + transform contractを使う。

### REMOVE / DOWNGRADE 4 — `latest` tool/image tag

再現性のため標準ではpinする。`latest`は探索環境でのみ許容し、検証済みproduction-like pipelineへ昇格させない。

### REMOVE / DOWNGRADE 5 — monolithic final render

長編全体を1 FFmpeg invocationへ積み込む方式は標準禁止。scene/chapter単位で成功物を積み上げる。

---

# Part C — 共通設計

## 8. AgentとVideo pipelineを同じDAG原則で扱う

長編動画とAI Armyは別問題に見えるが、信頼性の中心は同じである。

```text
Input
  -> Plan / Mission Manifest
  -> independent bounded tasks
  -> verified artifacts
  -> dependency join
  -> independent validation
  -> atomic promotion
  -> final integration
```

共通ルール:

- mutable shared stateを減らす
- ownerを1つにする
- idempotency keyを持つ
- verified artifactを再利用する
- retryはroot cause分類後に行う
- stale resultで新stateを上書きしない
- final successを文章ではなくmachine gateで決める

---

## 9. 新しい恒久目標

### AI Army

1. duplicate task execution caused by lease collision = 0
2. stale result overwrite = 0
3. irreversible tool call without blocking guardrail = 0
4. agent loop without termination budget = 0
5. majority-vote-only architecture decisions = 0
6. new session restoration success from repository canonical state = 100%
7. multi-agent change is promoted only when it beats or materially complements single-agent baseline
8. high-impact outputs have machine oracle or independent verifier evidence

### Long-form video

1. healthy previous scene regeneration after later failure = 0
2. successful checkpoint loss after downstream failure = 0
3. partial file accepted as verified scene = 0
4. subtitle full narration coverage = 100%
5. final ffprobe parse success = 100%
6. final decode error count = 0 when full decode gate is enabled
7. cache treated as only durable checkpoint = 0
8. unknown/freemium video SaaS execution = 0
9. VOICEVOX raw audio regeneration for visual-only change = 0
10. scene concat contract pass = 100%

---

## 10. Promotion rule

新しいノウハウは、次のうち2つ以上を満たして初めて恒久Policyへ昇格する。

- official/spec evidence
- peer-reviewed or major-lab research evidence
- reproducible repository test/trace evidence
- mature OSS operational evidence

ただし安全境界、課金境界、秘密値保護は単一の強い一次根拠でも即時fail-closedへできる。

古いルールが新Evidenceと矛盾した場合は、追加で積み重ねず、**置換・統合・削除**する。Policy肥大化そのものを避ける。
