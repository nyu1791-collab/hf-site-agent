# AI Army Multi-Agent Operating Standard

**Status:** Permanent operating standard  
**Effective:** 2026-09-12 JST  
**Machine policy:** `config/multi_agent_operating_policy.json`

この文書は、AI Armyを「Agent数が多いほど強い」Swarmへ変えるためのものではない。目的は、**必要なときだけ専門Agentを使い、中央司令・明確な契約・限定並列・独立検証・Checkpoint・Traceを組み合わせ、単一Agentより総合価値が上がる場合だけMulti-Agentを採用する**ことである。

---

## 1. 証拠の優先順位

設計変更は次の順で重く扱う。

1. 公式Framework / Provider仕様
2. 現行AI Armyで再現できるテスト・Trace・失敗事実
3. 成熟したOSSの実装パターン
4. 複数の独立した技術資料の一致
5. Agent自身の提案・多数決

Agentの多数決だけでArchitectureを変更しない。変更は必ず機械検証可能なAcceptance Criteriaへ落とす。

---

## 2. 調査した主要な一次・準一次資料

### OpenAI Agents SDK

- https://openai.github.io/openai-agents-python/
- https://openai.github.io/openai-agents-python/multi_agent/
- https://openai.github.io/openai-agents-python/guardrails/
- https://openai.github.io/openai-agents-python/tracing/

採用した要点:

- 中央ManagerがSpecialistをtoolとして使う方式と、会話制御自体をSpecialistへ渡すHandoffを分ける。
- AI Armyでは原則Manager方式。最終責任と安全GateをTop Commanderへ残す。
- Guardrailは入口/出口だけでは不十分。副作用Toolごとに検査する。
- Agent / Turn / Tool / HandoffをTraceし、失敗原因を後から再現できるようにする。

### Anthropic Engineering

- https://www.anthropic.com/engineering/multi-agent-research-system
- https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- https://www.anthropic.com/engineering

採用した要点:

- 複雑なResearchではLead Agentが複数Subagentを並列化し、Subagent自身も複数Toolを並列化することで大幅な時間短縮が可能。
- ただし、効果が出るのは独立に探索できるWorkstreamがある場合。
- Subagentは大量の探索Contextを抱えてよいが、親へ返すのは蒸留された結果とEvidence中心にする。
- Context windowを共有ゴミ箱にしない。Project stateは外部Artifact/Checkpointへ置く。
- 無限探索を防ぐGuardrailと停止条件が必須。

### Google Research: Scaling Agent Systems

- https://research.google/blog/towards-a-science-of-scaling-agent-systems/
- https://arxiv.org/abs/2512.08296

採用した要点:

- Multi-Agentの効果はTaskの並列性、単独Agent能力、Tool負荷、Topologyで変わる。
- Parallelizable workでは中央Coordinatorが有効だが、Sequential workではCoordination taxが利益を消し得る。
- Independent swarmよりCentralized topologyの方が誤りの増幅を抑えやすい。
- 論文の数値閾値をそのまま恒久Ruleにせず、AI Army固有の固定Fixtureで校正する。

### LangChain / LangGraph

- https://docs.langchain.com/oss/python/langchain/multi-agent
- https://docs.langchain.com/oss/python/langchain/multi-agent/subagents
- https://docs.langchain.com/oss/python/langchain/multi-agent/router

採用した要点:

- 複雑なTaskでもSingle Agent + 適切なToolで十分な場合がある。Multi-Agentを初期値にしない。
- Supervisor + stateless SubagentはContext isolationに有効。
- 明確なカテゴリ分類は軽量Router、文脈が進化する複数stepはSupervisorを使う。
- 独立TaskだけParallel化し、依存TaskはJoinを待つ。

### Microsoft AutoGen

- https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/core-concepts/agent-and-multi-agent-application.html
- https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/framework/message-and-communication.html
- https://microsoft.github.io/autogen/dev/user-guide/core-user-guide/core-concepts/application-stack.html
- https://microsoft.github.io/autogen/dev/user-guide/core-user-guide/design-patterns/mixture-of-agents.html

採用した要点:

- Agent間Messageは単なる文章ではなく**Behavior Contract**として扱う。
- Messageはserializable dataにし、隠れたlogicを埋め込まない。
- Coder → Executor → Reviewerのように役割を分け、失敗時だけ明示Reviewを返すReflection patternは有効。
- Layerを増やすほどContext・Token・Latencyが増えるため、Mixture/Debateは常用しない。

### CrewAI

- https://docs.crewai.com/
- https://docs.crewai.com/core-concepts/Agents

採用した要点:

- 自律協調が必要な部分はCrew、決定論的な分岐・状態管理・LoopはFlowとして扱う考え方が有効。
- AI ArmyではPython/DAG/Schema/Quota/Hash/Checkpoint等の決定論的処理をAgentへ戻さない。

### Google Agent2Agent (A2A)

- https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/
- https://developers.googleblog.com/build-cross-language-multi-agent-team-with-google-agent-development-kit-and-a2a/

採用した要点:

- 異なるProvider/Framework間ではCapability discoveryが必要。
- Remote Agent Taskはlifecycle/stateを持たせる。
- HTTP/SSE/JSON-RPC等の一般的標準を使う。
- 長時間Taskは進捗・状態更新・再開を前提にする。
- 認証/認可はAgent任せにせずProtocol境界で行う。

A2Aそのものを今すぐ必須化しない。Native AI Army contractを壊さず、将来Remote Agentを増やす場合の互換方向として採用する。

### NVIDIA

- https://developer.nvidia.com/blog/optimizing-data-center-performance-with-ai-agents-and-the-ooda-loop-strategy/
- https://developer.nvidia.com/blog/train-small-orchestration-agents-to-solve-big-problems/
- https://docs.nvidia.com/nemo/agent-toolkit/latest/components/agents/router-agent/index.html
- https://developer.nvidia.com/blog/route-ai-agent-workloads-across-models-with-nvidia-nemo-switchyard

採用した要点:

- Director / Manager / Workerの階層分離は、現在のTop Commander / Commander / Specialist-Worker構造と整合する。
- Orchestratorは一番大きいModelである必要はなく、Routing品質・Cost・Latencyを含む全体最適が重要。
- clear routeではsingle-pass routerが有効。
- Model routingはProviderから分離し、Task fit / 成功確率 / Cost / Latencyを評価する。ただし本プロジェクトはFree-only Gateを最優先する。

---

## 3. Multi-Agentを使う条件

以下の少なくとも1つが成立する場合だけ採用候補とする。

- 明確に異なる専門領域がある。
- 互いに依存しない探索/検証Workstreamを並列化できる。
- 独立Reviewerが重大な誤りを減らす価値が高い。
- 大量ContextをSubagentへ隔離した方がTop Commanderの判断品質が上がる。
- 長時間TaskをCheckpoint付きで分業する必要がある。

以下は原則Multi-Agent化しない。

- Sort / Hash / JSON Schema / Quota / Retry / Cache / deterministic validation。
- 1回のTool callや短い変換で終わるTask。
- 同じ質問を3Agentへ投げて多数決するだけの構成。
- Coordination costの方が専門化Benefitより大きいTask。

**必ずSingle-Agent baselineと比較する。** Agent数増加そのものを成功指標にしない。

Architectureを昇格する前に、parallelizable fraction、single-agent baseline
quality、tool intensity、shared-state risk、verification risk、coordination
overhead、latency、cost、provider healthを記録する。欠落がある提案はShadow
recommendationに留める。

---

## 4. 正式な指揮系統

```text
ChatGPT Work / Top Commander
        |
        +-- Google Corps      : Research / Long Context / Multimodal
        +-- NVIDIA Corps      : Engineering / Review / Debug / Repository
        +-- Groq Corps        : Fast structured work / extraction / classification
                |
                +-- Specialist
                        |
                        +-- bounded Worker
```

- Rootは1つ。
- Task ownerも1つ。
- Worker間の直接委任は禁止。
- Writerも原則1つ。
- Specialistは親のScopeを超えない。
- 子Agentが親より強い権限を持たない。

---

## 5. Agent間Contract

自然言語だけで委任しない。Command Envelopeには最低限以下を持たせる。

- `mission_id`
- `task_id`
- `owner_agent_id`
- objective
- inputs / artifact references
- allowed tools
- forbidden actions
- request/token/time budget
- acceptance criteria
- idempotency key

Report Envelopeには最低限以下を持たせる。

- status
- distilled result
- produced artifacts
- evidence/provenance
- assumptions
- unresolved risks
- usage / retries
- next recommended action

Reportに内部探索全文を貼らない。必要なEvidenceはArtifact/referenceへ逃がし、親Contextへは要約だけを戻す。

---

## 6. Context Engineering

### Context quarantine

Subagentへ渡すのはTaskに必要な最小Contextだけにする。全会話・全Repository・全Tool logを全AgentへBroadcastしない。

### State separation

- Conversation memory: 会話理解用。
- Mission state: DAG / status / owner / dependency用。
- Artifact state: file / evidence / generated output用。
- Checkpoint state: recovery用。

これらを混ぜない。

### Distillation

Subagentは大量に調査してもよいが、親へ返すのは「結論 + 根拠 + 未解決 + Artifact参照」を基本とする。

---

## 7. Parallelism

Parallel化してよいのは**独立Taskだけ**。

実行前に完全なDependency DAGを作り、Task ID重複・不明Dependency・Cycleを
fail closedで止める。Queueは明示priority、user-visible性、推定実行時間を
含むremaining critical pathで決める。将来Waveの計画は高速化のための予測で
あり、Runtimeでは各Dependencyのverified artifactが揃うまで次Taskを解放しない。

良い例:

- Research A / Research B / Compatibility Checkを並列。
- Video Scene 1 / 2 の素材検査を、同一writerを使わず並列。

悪い例:

- 前Taskの出力が必要なのに同時起動。
- 複数Agentが同じfileへ同時write。
- 同じTaskを無差別に複製してTokenを消費。

Dependency JoinをDAG側で管理し、Agentの「たぶん終わった」で先へ進めない。

---

## 8. Review / Reflection / Debate

標準は **Primary + Independent Verifier**。

ReviewerはAcceptance CriteriaとEvidenceを受け取り、次を返す。

- PASS
- REVISION: 失敗した基準と修正点
- BLOCKED: 外部条件不足

同じ失敗を繰り返したら再作文ではなくRoot CauseとReplanへ移る。

Multi-Agent Debateはデフォルト禁止。曖昧なReasoning課題で明確な利益がある場合のみ、Round数を有限にして使う。

---

## 9. Reliability / Recovery

- meaningful stageごとにCheckpoint。
- side effectはIdempotency key必須。
- retry時はpayload hash一致を確認。
- 古いAgent結果で新しいstateを上書きしない。
- Provider failureはCircuit隔離。
- 1 Taskの失敗で別Missionや健康なArtifactを削除しない。
- 完了済みのPartial Successを保存する。

長時間Taskでは「最初からやり直す」より「最後の検証済みCheckpointから再開」を優先する。

---

## 10. Safety / Authority

Agentに必要最低限のPermissionだけを付与する。

Workerには以下を与えない。

- payment / auto top-up
- secret readback / mutation
- main push
- PR merge
- deploy / publish
- Durable Object変更
- irreversible external action

これらはHuman Escalationで停止する。

またGuardrailはAgent入口/最終Outputだけでなく、**副作用Toolの直前**にも置く。

---

## 11. Observability

Mission → Task → Agent → Turn → Tool/HandoffのTrace IDをつなぐ。

最低限記録する。

- provider/model route
- start/end/latency
- token/request count
- retry / 429 / 5xx
- cost or verified zero-cost evidence
- tool call result
- guardrail decision
- approval decision
- checkpoint save/restore
- replan reason

Secretや機密payloadはTraceへ残さない。

---

## 12. Evaluation

個別Model benchmarkだけで採用しない。**システム全体**を固定Mission setで比較する。

必須比較:

1. Single Agent baseline
2. Current AI Army
3. Candidate multi-agent change

測定値:

- task success
- acceptance criteria pass
- schema pass
- tool success
- latency
- token/request count
- 429 rate
- cost
- handoff count
- replan count
- checkpoint recovery success
- stale-write prevention

Multi-Agent変更は、AccuracyだけでなくLatency/Cost/Failure recoveryまで含めた総合価値が改善したときだけ採用する。

---

## 13. Frameworkの使い分け

Native AI Army RuntimeをSource of Truthとして残す。

- **LangGraph:** 複雑なMission graph、checkpoint、stateful orchestration。
- **CrewAI:** 明確な専門Crewを短く組む場合。
- **AutoGen:** message-driven collaboration、bounded council/reflectionを試験する場合。
- **GitHub Copilot Agent:** repository codingの限定Adapter。

FrameworkへArchitectureを乗っ取らせない。AdapterはCommand/Report contractへ変換してNative Runtimeに戻す。

---

## 14. 長編動画制作への適用

長編動画では次の分業を標準候補とする。

- Top Commander: 構成、Task DAG、採用判断、最終統合。
- Research Specialist: 情報・素材候補・出典。
- Script Specialist: approved factsから台本生成。
- Technical Reviewer: FFmpeg / codec / subtitle / recovery監査。
- Asset Validator: download/decode/rights/size検査。
- Python/FFmpeg: deterministic render。

**レンダリング自体をAgent会話で行わない。** AIは計画・生成・監査に使い、Media encodingはPython/FFmpegへ渡す。

SceneごとにArtifact contractを持ち、成功Sceneを再生成しない。これは`config/longform_video_objectives.json`と`config/longform_video_reliability_policy.json`を優先する。

---

## 15. 今後の実装優先順位

1. Command/Report envelopeへEvidence / assumptions / unresolved risksを厳格化。
2. 全Agent境界へのTrace ID propagation。
3. Multi-Agent admission gateをruntime routingへ追加。
4. Single-Agent baseline自動比較。
5. Provider/model routeの成功率・Latency・無料可用性を履歴化。
6. Stale result guardを全writer pathへ適用。
7. Remote Agentを導入するときだけA2A-style capability card / task lifecycleをAdapterとして追加。

この順序を飛ばしてAgent数だけ増やさない。
