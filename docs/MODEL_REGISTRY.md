# Role-based Model Registry — Technical Reference

**Status:** implementation reference, not live Provider/readiness authority.

`config/model_registry.json` はRoleとModelの候補・互換情報を分離するMachine-readable台帳です。ただし、この文書やRegistryに過去から残る候補名・期待ID・互換IDだけでは、現在のModel availability、free status、quota、cost、Provider readiness、routing authorityを証明しません。

現在の実行権限とRoutingは `config/current_commander_handoff.json`、`config/permanent_standards_manifest.json`、`docs/AI_ARMY_MASTER_RULEBOOK.md`、関連Machine Policy、`scripts/ai_army_routing_facade.py` を優先します。外部Provider利用前はcurrent catalog/account evidenceを再取得し、古い文書の数値やModel名を現在値として流用しません。

## Lifecycle と Discovery

評価Recordは、実装が対応する範囲でModel identity、Provider、Role candidate、Capability、Lifecycle、発見時刻、最終検証時刻、Probe、Benchmark、Cost、Quota等を保持します。未検証値は推測せず `null` / `UNKNOWN` / `NOT_RUN` 等として保持します。

標準的な昇格順は次です。

`DISCOVERED → CAPABILITY_CHECKED → COST_CHECKED → PROBED → BENCHMARKED → CANDIDATE → EXPLICIT_APPROVAL → ACTIVE`

重要な境界:

- DiscoveryだけでActiveにしない。
- 名前が似ているModelへ自動置換しない。
- Exact Model IDを現在のProvider Catalogまたは公式Account evidenceで確認する。
- Free/zero-cost、Quota、Capability、Route bindingを別々に検証する。
- Probe成功はProduction activationを意味しない。
- Historical `enabled` / candidate / compatibility値は現在の実行権限にならない。
- Unknown cost/quota/paid transitionはfail-closed。

## Expected / Compatibility records

過去のPhaseや移行作業で、ユーザー指定または期待候補を `EXPECTED_UNVERIFIED`、旧Modelをcompatibility/legacy情報として残す場合があります。これは検証時の照合や回帰テストのための情報であり、存在証明・Free証明・Role割当・Fallback許可・ACTIVE登録ではありません。

古い固定RoleやModel IDを、現在の `primary_model`、fallback、candidate、routing authorityへ復活させてはいけません。Legacy情報が必要な理由は「以前の経路を再発させないことを検査する」ためです。

## Free route の選定原則

Free routeを使う場合も固定IDを盲信せず、利用時点のCatalog/evidenceから厳格に判定します。実装に応じて少なくとも以下を確認します。

1. Exact Model IDとProvider endpoint/route。
2. 現在のFree/zero-cost evidence。
3. 現在のQuota / account eligibility evidence。
4. 必要CapabilityとContext。
5. Provider fallback / paid transitionが無効であること。
6. Retry・Request・Token・Concurrencyがboundedであること。
7. Probe応答Modelが要求IDと一致すること。
8. Usage/cost evidenceがPolicy条件を満たすこと。

`:free`等のラベルだけで重大判断や最終Reviewを許可しません。モデル名・Catalog掲載・古い成功Artifactだけでも現在の実行許可にはなりません。

## Evaluation record projection

既存Schema/実装との互換性のため、`scripts/model_registry.py` 等がMachine-readable Registryを評価用Recordへ射影することがあります。射影はRegistryのAuthorityを拡大せず、Lifecycle、Role candidate、Capability、Probe、Benchmark、Cost、Quota等を構造化して比較するためのものです。

射影処理が不明値を補完・推測したり、historical recordを現在のProvider evidenceへ昇格させたりしてはいけません。

## Provider / Quota の可変値

Provider classification、RPM/TPM/RPD/TPD、daily cap、Hard Stop、credits、rate-limit header、free-tier条件、Model ID等は時間とAccountで変わり得ます。この文書へ固定値を恒久ルールとして複製しません。

実行時は以下を優先します。

- current Machine-readable Registry / Policy
- current official Provider Catalog / pricing / quota evidence
- current account-specific evidence when required
- current exact-route Probe result
- current safety/cost gate

過去に使用したOpenRouter等の数値制限は回帰FixtureやLegacy configに残る場合がありますが、別Providerへ流用せず、現在値として扱いません。

## Activation boundary

Provider/Role/ModelのActive化は、少なくともcurrent evidence、必要Capability、Cost/Quota safety、bounded Probe、Routing contract、Human Approval Gate等の現行Policy条件を満たした場合だけ候補になります。

Registry、Probe、Benchmark、文書のいずれか1つだけでProduction activation、Paid fallback、Deploy、Publish、Secrets操作を許可することはありません。ChatGPT / Workが最終Authorityを保持します。
