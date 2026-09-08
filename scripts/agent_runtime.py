#!/usr/bin/env python3
"""Deterministic runtime primitives for the hierarchical AI command chain.

This module deliberately contains no model calls.  It validates parent -> child
commands, applies least-privilege tool scopes, bounds fan-out, and keeps small
artifacts/checkpoints/trace records.  Model adapters and GitHub Actions remain
responsible for the actual draft generation; this runtime only dispatches and
records work.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
import hashlib
import json
from pathlib import Path
import re
from threading import Event, RLock
import time
from typing import Any, Callable, Iterable, Mapping, Sequence


MAX_TEXT = 2_000
MAX_JSON_CHARS = 50_000
MAX_CONTEXT_CHARS = 8_000
MAX_CHILDREN = 5
MAX_PARALLEL = 4
MAX_DEPTH = 4
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
SECRET_RE = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._~+/=-]{12,}|"
    r"(?:sk|gsk|hf|ghp|github_pat|sk-or-v1)[_-][A-Za-z0-9_-]{12,})"
)
SECRET_KEY_RE = re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|password|secret)")


class RuntimeErrorBase(ValueError):
    """Base class for errors that should be reported to the commander."""


class ContractError(RuntimeErrorBase):
    pass


class PermissionError(RuntimeErrorBase):
    pass


class BudgetError(RuntimeErrorBase):
    pass


class AgentRank(IntEnum):
    COMMANDER = 0
    UPPER_COMMANDER = 1
    SPECIALIST_COMMANDER = 2
    WORKER = 3


COMMAND_STATUSES = {
    "queued",
    "running",
    "completed",
    "completed_with_warnings",
    "blocked",
    "failed",
    "cancelled",
}
REPORT_STATUSES = COMMAND_STATUSES - {"queued", "running"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_text(value: Any, limit: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    value = str(value).replace("\x00", "").strip()
    if SECRET_RE.search(value):
        raise ContractError("secret-shaped value is not allowed in an envelope")
    return value[:limit]


def _walk_safe(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if SECRET_KEY_RE.search(key_text) and key_text not in {
                "permissions",
                "prohibited",
                "safety_flags",
                "required_confirmation",
            }:
                raise ContractError(f"secret-like field is not allowed: {path}.{key_text}")
            _walk_safe(item, f"{path}.{key_text}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _walk_safe(item, f"{path}[{index}]")
    elif isinstance(value, str) and SECRET_RE.search(value):
        raise ContractError(f"secret-shaped value is not allowed: {path}")


def safe_json(value: Any, limit: int = MAX_JSON_CHARS) -> Any:
    _walk_safe(value)
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(encoded) > limit:
        raise ContractError("envelope payload is too large")
    return value


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def stable_id(prefix: str, value: Any, length: int = 12) -> str:
    clean = re.sub(r"[^A-Za-z0-9._:-]+", "-", prefix).strip("-") or "ID"
    return f"{clean}-{stable_hash(value)[:length]}"


def _tuple_strings(values: Iterable[Any] | None, limit: int = 32, item_limit: int = MAX_TEXT) -> tuple[str, ...]:
    if values is None:
        return ()
    output: list[str] = []
    for value in list(values)[:limit]:
        text = safe_text(value, item_limit)
        if text:
            output.append(text)
    return tuple(output)


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    parent_agent_id: str | None
    rank: int
    role: str
    mission: str
    allowed_children: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    working_directory: str = "artifacts"
    context_budget: int = 4_000
    token_budget: int = 500
    time_budget_ms: int = 15_000
    max_children: int = MAX_CHILDREN
    max_parallel: int = MAX_PARALLEL
    max_depth: int = MAX_DEPTH
    permissions: tuple[str, ...] = ()
    report_schema: str = "report-envelope-v1"
    may_spawn_children: bool = False
    allowed_child_roles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not ID_RE.fullmatch(self.agent_id):
            raise ContractError(f"invalid agent_id: {self.agent_id}")
        if self.parent_agent_id is not None and not ID_RE.fullmatch(self.parent_agent_id):
            raise ContractError(f"invalid parent_agent_id: {self.parent_agent_id}")
        if self.rank < 0 or self.rank > int(AgentRank.WORKER):
            raise ContractError("agent rank is outside the hierarchy")
        if not 1 <= self.context_budget <= MAX_CONTEXT_CHARS:
            raise ContractError("context budget is outside bounds")
        if not 1 <= self.token_budget <= 8_000:
            raise ContractError("token budget is outside bounds")
        if not 1 <= self.time_budget_ms <= 300_000:
            raise ContractError("time budget is outside bounds")
        if not 0 <= self.max_children <= MAX_CHILDREN:
            raise ContractError("max_children is outside bounds")
        if not 1 <= self.max_parallel <= MAX_PARALLEL:
            raise ContractError("max_parallel is outside bounds")
        if not 0 <= self.max_depth <= MAX_DEPTH:
            raise ContractError("max_depth is outside bounds")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_agent_specs() -> tuple[AgentSpec, ...]:
    """The current chain, plus bounded specialist/worker slots.

    GLM and DeepSeek are independent upper commanders invoked in bounded
    parallel by the sole commander.  Specialist commands are still issued only
    after commander approval; the registry does not create a peer-to-peer or
    promotion route.
    """

    specialist_roles = ("research", "product", "content", "video", "code", "qa", "metrics")
    general_roles = {"research", "product", "content"}
    tool_map: dict[str, tuple[str, ...]] = {
        "research": ("public_web_read", "artifact_read", "artifact_write", "trace"),
        "product": ("artifact_read", "artifact_write", "trace"),
        "content": ("artifact_read", "artifact_write", "trace"),
        "video": ("artifact_read", "artifact_write", "colab_plan", "trace"),
        "code": ("repo_read", "tests_read", "artifact_read", "artifact_write", "trace"),
        "qa": ("repo_read", "tests_read", "artifact_read", "artifact_write", "trace"),
        "metrics": ("artifact_read", "artifact_write", "trace"),
    }
    specs: list[AgentSpec] = [
        AgentSpec(
            "chatgpt-work", None, AgentRank.COMMANDER, "commander",
            "全体Missionの解釈・承認・統合・最終実行判断",
            # GLM and DeepSeek receive independent work from the sole
            # commander.  They never dispatch to one another.
            allowed_children=("glm-general-commander", "deepseek-engineering-commander"),
            allowed_tools=("approval", "github_read", "github_write", "artifact_read", "artifact_write", "queue", "trace"),
            working_directory="artifacts/commander", context_budget=8_000, token_budget=8_000,
            time_budget_ms=300_000, max_children=2, max_parallel=2, max_depth=MAX_DEPTH,
            permissions=("approve", "pr", "deploy_after_review", "publish_after_review"),
            report_schema="report-envelope-v1", may_spawn_children=True,
            allowed_child_roles=("upper_commander",),
        ),
        AgentSpec(
            "glm-general-commander", "chatgpt-work", AgentRank.UPPER_COMMANDER, "upper_commander",
            "需要・製品・コンテンツ側のMissionを独立に分解し、専門指揮へ命令する",
            allowed_children=("research-specialist", "product-specialist", "content-specialist"),
            allowed_tools=("model:role-registry", "artifact_read", "artifact_write", "trace"),
            working_directory="artifacts/planner", context_budget=6_000, token_budget=320,
            time_budget_ms=12_000, max_children=3, max_parallel=3, max_depth=MAX_DEPTH,
            permissions=("propose", "decompose", "draft"), report_schema="report-envelope-v1",
            may_spawn_children=True, allowed_child_roles=("specialist_commander",),
        ),
        AgentSpec(
            "deepseek-engineering-commander", "chatgpt-work", AgentRank.UPPER_COMMANDER, "upper_commander",
            "技術・品質・自動化側の独立レビューと専門指揮Agent向け作業指示",
            allowed_children=("video-specialist", "code-specialist", "qa-specialist", "metrics-specialist"),
            allowed_tools=("model:role-registry", "artifact_read", "artifact_write", "trace"),
            working_directory="artifacts/critic", context_budget=6_000, token_budget=320,
            time_budget_ms=12_000, max_children=4, max_parallel=4, max_depth=MAX_DEPTH,
            permissions=("review", "decompose", "draft"), report_schema="report-envelope-v1",
            may_spawn_children=True, allowed_child_roles=("specialist_commander",),
        ),
    ]
    for role in specialist_roles:
        child = f"{role}-worker"
        parent_id = "glm-general-commander" if role in general_roles else "deepseek-engineering-commander"
        specs.append(
            AgentSpec(
                f"{role}-specialist", parent_id, AgentRank.SPECIALIST_COMMANDER, "specialist_commander",
                f"{role}領域の一件の承認済みTaskを下書き化して報告",
                allowed_children=(child,), allowed_tools=tool_map[role],
                working_directory=f"artifacts/{role}", context_budget=4_000, token_budget=500,
                time_budget_ms=15_000, max_children=1, max_parallel=1, max_depth=MAX_DEPTH,
                permissions=("draft", "report"), report_schema="report-envelope-v1",
                may_spawn_children=True, allowed_child_roles=("worker",),
            )
        )
        specs.append(
            AgentSpec(
                child, f"{role}-specialist", AgentRank.WORKER, "worker",
                f"{role}の小さな通常コード・参照処理",
                allowed_children=(), allowed_tools=("filesystem_read", "artifact_read", "artifact_write", "trace"),
                working_directory=f"artifacts/{role}/worker", context_budget=2_000, token_budget=200,
                time_budget_ms=10_000, max_children=0, max_parallel=1, max_depth=MAX_DEPTH,
                permissions=("execute_bounded_unit", "report"), report_schema="report-envelope-v1",
                may_spawn_children=False, allowed_child_roles=(),
            )
        )
    return tuple(specs)


class AgentRegistry:
    def __init__(self, specs: Sequence[AgentSpec] | None = None) -> None:
        self._specs = {spec.agent_id: spec for spec in (specs or default_agent_specs())}
        self.validate()

    def validate(self) -> None:
        if "chatgpt-work" not in self._specs:
            raise ContractError("commander root is missing")
        for spec in self._specs.values():
            if spec.parent_agent_id is None:
                if spec.rank != AgentRank.COMMANDER:
                    raise ContractError(f"only the root may have no parent: {spec.agent_id}")
            else:
                parent = self._specs.get(spec.parent_agent_id)
                if parent is None:
                    raise ContractError(f"parent agent is missing: {spec.agent_id}")
                if spec.agent_id not in parent.allowed_children:
                    raise PermissionError(f"parent does not allow child: {parent.agent_id} -> {spec.agent_id}")
                # GLM and DeepSeek are sibling upper-command agents
                # under ChatGPT Work.  Equal rank is valid; only direct
                # parent-to-child dispatch is permitted and upward/peer edges
                # are rejected.
                if spec.rank < parent.rank:
                    raise ContractError(f"child rank may not be above parent: {parent.agent_id} -> {spec.agent_id}")
            if len(set(spec.allowed_children)) != len(spec.allowed_children):
                raise ContractError(f"duplicate child declaration: {spec.agent_id}")
            if spec.may_spawn_children and not spec.allowed_children:
                raise ContractError(f"agent marked spawn-capable without child allow-list: {spec.agent_id}")
            if not set(spec.allowed_child_roles).issubset({self._specs[c].role for c in spec.allowed_children}):
                raise ContractError(f"allowed_child_roles contains an unlisted role: {spec.agent_id}")
        for agent_id in self._specs:
            seen: set[str] = set()
            current: str | None = agent_id
            while current is not None:
                if current in seen:
                    raise ContractError("agent hierarchy contains a cycle")
                seen.add(current)
                current = self._specs[current].parent_agent_id

    def get(self, agent_id: str) -> AgentSpec:
        try:
            return self._specs[agent_id]
        except KeyError as exc:
            raise ContractError(f"unknown agent: {agent_id}") from exc

    def all(self) -> tuple[AgentSpec, ...]:
        return tuple(self._specs.values())

    def can_dispatch(self, parent_agent_id: str, child_agent_id: str) -> bool:
        parent = self.get(parent_agent_id)
        child = self.get(child_agent_id)
        return child.parent_agent_id == parent.agent_id and child.agent_id in parent.allowed_children

    def assert_dispatchable(self, parent_agent_id: str, child_agent_id: str, tools: Sequence[str], depth: int) -> None:
        parent = self.get(parent_agent_id)
        child = self.get(child_agent_id)
        if not self.can_dispatch(parent_agent_id, child_agent_id):
            raise PermissionError(f"command may flow only to an allowed direct child: {parent.agent_id} -> {child.agent_id}")
        if depth > child.max_depth or depth > MAX_DEPTH:
            raise ContractError("maximum agent depth exceeded")
        if not set(tools).issubset(set(child.allowed_tools)):
            raise PermissionError(f"tool scope exceeds child allowance: {child.agent_id}")
        if child.agent_id != "chatgpt-work" and child.rank < parent.rank:
            raise PermissionError("commands may not move upward")
        if not child.may_spawn_children and child.allowed_children:
            raise ContractError("child allow-list is inconsistent with spawn permission")

    def tree(self) -> str:
        children: dict[str | None, list[AgentSpec]] = {}
        for spec in self._specs.values():
            children.setdefault(spec.parent_agent_id, []).append(spec)
        lines: list[str] = []

        def visit(parent: str | None, prefix: str = "") -> None:
            for index, spec in enumerate(sorted(children.get(parent, []), key=lambda item: item.agent_id)):
                branch = "└─" if index == len(children.get(parent, [])) - 1 else "├─"
                lines.append(f"{prefix}{branch} {spec.agent_id} [{spec.role}, rank={spec.rank}]")
                visit(spec.agent_id, prefix + ("  " if branch == "└─" else "│ "))

        root = self.get("chatgpt-work")
        lines.append(f"{root.agent_id} [{root.role}, rank={root.rank}]")
        visit(root.agent_id)
        return "\n".join(lines)


@dataclass(frozen=True)
class CommandEnvelope:
    mission_id: str
    command_id: str
    parent_command_id: str | None
    parent_agent_id: str
    child_agent_id: str
    owner_agent_id: str
    rank: int
    role: str
    mission: str
    objective: str
    constraints: tuple[str, ...] = ()
    inputs: Mapping[str, Any] = field(default_factory=dict)
    input_refs: tuple[str, ...] = ()
    expected_output: Mapping[str, Any] = field(default_factory=dict)
    deadline: str | None = None
    token_budget: int = 1
    time_budget_ms: int = 1_000
    tool_scope: tuple[str, ...] = ()
    may_spawn_children: bool = False
    allowed_child_roles: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    parallel_group: str | None = None
    priority: int = 0
    depth: int = 1
    max_depth: int = MAX_DEPTH
    done_when: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    idempotency_key: str | None = None
    created_at: str = field(default_factory=now_iso)
    status: str = "queued"

    def validate(self, registry: AgentRegistry) -> None:
        for label, value in (("mission_id", self.mission_id), ("command_id", self.command_id), ("parent_agent_id", self.parent_agent_id), ("child_agent_id", self.child_agent_id), ("owner_agent_id", self.owner_agent_id)):
            if not ID_RE.fullmatch(value):
                raise ContractError(f"invalid {label}")
        if self.parent_command_id is not None and not ID_RE.fullmatch(self.parent_command_id):
            raise ContractError("invalid parent_command_id")
        if self.owner_agent_id != self.child_agent_id:
            raise ContractError("owner_agent_id must equal child_agent_id")
        child = registry.get(self.child_agent_id)
        if self.rank != child.rank or self.role != child.role:
            raise ContractError("command rank/role does not match child agent")
        registry.assert_dispatchable(self.parent_agent_id, self.child_agent_id, self.tool_scope, self.depth)
        if self.may_spawn_children != child.may_spawn_children:
            raise PermissionError("command spawn permission does not match registry")
        if not set(self.allowed_child_roles).issubset(set(child.allowed_child_roles)):
            raise PermissionError("command child-role scope exceeds registry")
        if self.max_depth > child.max_depth:
            raise ContractError("command max_depth exceeds agent max_depth")
        if not self.mission or not self.objective or not self.done_when:
            raise ContractError("mission, objective, and done_when are required")
        if len(self.mission) > MAX_TEXT or len(self.objective) > MAX_TEXT:
            raise ContractError("command text is too long")
        if not 1 <= self.token_budget <= child.token_budget:
            raise BudgetError("command token budget exceeds child budget")
        if not 1 <= self.time_budget_ms <= child.time_budget_ms:
            raise BudgetError("command time budget exceeds child budget")
        if self.depth < 1 or self.depth > self.max_depth:
            raise ContractError("command depth is invalid")
        if self.status not in COMMAND_STATUSES:
            raise ContractError("unknown command status")
        if self.priority < -100 or self.priority > 100:
            raise ContractError("priority is outside bounds")
        safe_json(dict(self.inputs), limit=MAX_JSON_CHARS)
        safe_json(dict(self.expected_output), limit=MAX_JSON_CHARS)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["inputs"] = dict(self.inputs)
        value["expected_output"] = dict(self.expected_output)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CommandEnvelope":
        data = dict(value)
        for key in ("constraints", "input_refs", "tool_scope", "allowed_child_roles", "depends_on", "done_when", "permissions"):
            data[key] = _tuple_strings(data.get(key))
        data["inputs"] = dict(data.get("inputs") or {})
        data["expected_output"] = dict(data.get("expected_output") or {})
        return cls(**data)


@dataclass(frozen=True)
class ReportEnvelope:
    mission_id: str
    command_id: str
    parent_command_id: str | None
    agent_id: str
    parent_agent_id: str
    rank: int
    status: str
    summary: str
    result: Mapping[str, Any] = field(default_factory=dict)
    artifacts: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    children_used: tuple[str, ...] = ()
    duration_ms: int = 0
    tokens_used: int = 0
    cache_hit: bool = False
    tools_used: tuple[str, ...] = ()
    source_version: str = "agent-runtime-v1"
    created_at: str = field(default_factory=now_iso)

    def validate(self, command: CommandEnvelope, registry: AgentRegistry) -> None:
        if self.mission_id != command.mission_id or self.command_id != command.command_id:
            raise ContractError("report does not belong to command")
        if self.parent_command_id != command.parent_command_id:
            raise ContractError("report parent_command_id does not match command")
        if self.agent_id != command.child_agent_id or self.parent_agent_id != command.parent_agent_id:
            raise ContractError("report agent ownership does not match command")
        if self.rank != command.rank or self.status not in REPORT_STATUSES:
            raise ContractError("report status or rank is invalid")
        child = registry.get(self.agent_id)
        if not set(self.tools_used).issubset(set(child.allowed_tools)):
            raise PermissionError("report tool list exceeds child allowance")
        if not 0 <= self.duration_ms <= command.time_budget_ms:
            raise BudgetError("report duration exceeds command budget")
        if not 0 <= self.tokens_used <= command.token_budget:
            raise BudgetError("report tokens exceed command budget")
        if len(self.summary) > MAX_TEXT:
            raise ContractError("report summary is too long")
        safe_json(dict(self.result), limit=MAX_JSON_CHARS)
        _walk_safe({"summary": self.summary, "warnings": self.warnings, "errors": self.errors})

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["result"] = dict(self.result)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReportEnvelope":
        data = dict(value)
        for key in ("artifacts", "evidence", "warnings", "errors", "children_used", "tools_used"):
            data[key] = _tuple_strings(data.get(key))
        data["result"] = dict(data.get("result") or {})
        return cls(**data)


@dataclass(frozen=True)
class MissionBudget:
    token_budget: int = 5_000
    call_budget: int = 20
    time_budget_ms: int = 300_000
    max_depth: int = MAX_DEPTH

    def validate(self) -> None:
        if self.token_budget < 1 or self.call_budget < 1 or self.time_budget_ms < 1:
            raise BudgetError("mission budget must be positive")
        if not 1 <= self.max_depth <= MAX_DEPTH:
            raise BudgetError("mission max_depth is outside bounds")


class BudgetLedger:
    def __init__(self, budget: MissionBudget) -> None:
        budget.validate()
        self.budget = budget
        self._reserved_tokens = 0
        self._reserved_calls = 0
        self._used_tokens = 0
        self._used_calls = 0
        self._lock = RLock()

    def reserve(self, tokens: int, calls: int = 1) -> None:
        with self._lock:
            if tokens < 0 or calls < 0:
                raise BudgetError("negative budget reservation")
            if self._used_tokens + self._reserved_tokens + tokens > self.budget.token_budget:
                raise BudgetError("mission token budget exhausted")
            if self._used_calls + self._reserved_calls + calls > self.budget.call_budget:
                raise BudgetError("mission call budget exhausted")
            self._reserved_tokens += tokens
            self._reserved_calls += calls

    def settle(self, reserved_tokens: int, reserved_calls: int, used_tokens: int, used_calls: int = 1) -> None:
        with self._lock:
            self._reserved_tokens = max(0, self._reserved_tokens - reserved_tokens)
            self._reserved_calls = max(0, self._reserved_calls - reserved_calls)
            self._used_tokens += max(0, used_tokens)
            self._used_calls += max(0, used_calls)
            if self._used_tokens > self.budget.token_budget or self._used_calls > self.budget.call_budget:
                raise BudgetError("mission budget exceeded")

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "token_budget": self.budget.token_budget,
                "call_budget": self.budget.call_budget,
                "time_budget_ms": self.budget.time_budget_ms,
                "tokens_used": self._used_tokens,
                "calls_used": self._used_calls,
                "tokens_reserved": self._reserved_tokens,
                "calls_reserved": self._reserved_calls,
            }


class ArtifactStore:
    """Small content-addressed store; commands pass IDs instead of large text."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else None
        if self.root:
            self.root.mkdir(parents=True, exist_ok=True)
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def put(self, value: Any, kind: str = "artifact", mission_id: str = "", source_version: str = "") -> str:
        safe_json(value)
        payload = {"kind": safe_text(kind, 80), "mission_id": safe_text(mission_id, 128), "source_version": safe_text(source_version, 120), "value": value, "created_at": now_iso()}
        artifact_id = f"artifact-{stable_hash(payload)[:24]}"
        with self._lock:
            self._items[artifact_id] = payload
            if self.root:
                path = self.root / f"{artifact_id}.json"
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                tmp.replace(path)
        return artifact_id

    def get(self, artifact_id: str) -> Any:
        if not re.fullmatch(r"artifact-[0-9a-f]{24}", artifact_id):
            raise ContractError("invalid artifact_id")
        with self._lock:
            if artifact_id in self._items:
                return self._items[artifact_id]["value"]
            if self.root:
                path = self.root / f"{artifact_id}.json"
                if path.exists():
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    safe_json(payload)
                    self._items[artifact_id] = payload
                    return payload["value"]
        raise KeyError(artifact_id)


class HierarchicalCache:
    LEVELS = ("global", "mission", "commander", "worker")

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], tuple[float, Any]] = {}
        self._hits = 0
        self._misses = 0
        self._lock = RLock()

    def key(self, level: str, input_hash: str, agent_id: str = "", prompt_version: str = "", model: str = "") -> tuple[str, str]:
        if level not in self.LEVELS or not HASH_RE.fullmatch(input_hash):
            raise ContractError("invalid cache key")
        return level, stable_hash({"input_hash": input_hash, "agent_id": agent_id, "prompt_version": prompt_version, "model": model})

    def get(self, key: tuple[str, str], now: float | None = None) -> Any | None:
        current = time.monotonic() if now is None else now
        with self._lock:
            entry = self._entries.get(key)
            if not entry or (entry[0] and entry[0] <= current):
                self._misses += 1
                return None
            self._hits += 1
            return entry[1]

    def set(self, key: tuple[str, str], value: Any, ttl_seconds: int = 3_600) -> None:
        if ttl_seconds < 1 or ttl_seconds > 86_400:
            raise ContractError("cache TTL is outside bounds")
        safe_json(value)
        with self._lock:
            self._entries[key] = (time.monotonic() + ttl_seconds, value)

    def stats(self) -> dict[str, float | int]:
        with self._lock:
            total = self._hits + self._misses
            return {"hits": self._hits, "misses": self._misses, "hit_rate": self._hits / total if total else 0.0}


class CheckpointStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else None
        if self.root:
            self.root.mkdir(parents=True, exist_ok=True)
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def save(self, mission_id: str, stage: str, state: Mapping[str, Any]) -> str:
        if not ID_RE.fullmatch(mission_id) or not ID_RE.fullmatch(stage):
            raise ContractError("invalid checkpoint identity")
        payload = {"mission_id": mission_id, "stage": stage, "state": dict(state), "saved_at": now_iso()}
        safe_json(payload)
        key = f"{mission_id}:{stage}"
        with self._lock:
            self._items[key] = payload
            if self.root:
                path = self.root / f"{stable_hash(key)[:24]}.json"
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                tmp.replace(path)
        return stable_hash(payload)

    def load(self, mission_id: str, stage: str) -> dict[str, Any] | None:
        key = f"{mission_id}:{stage}"
        with self._lock:
            return self._items.get(key)


def project_context(mission: str, constraints: Sequence[str], relevant_state: Mapping[str, Any], input_refs: Sequence[str], output_schema: str, budget: int = MAX_CONTEXT_CHARS) -> dict[str, Any]:
    """Build the minimum inheritance contract; never copies parent history."""
    result = {
        "MISSION": safe_text(mission, 1_500),
        "CONSTRAINTS": list(_tuple_strings(constraints, limit=16, item_limit=400)),
        "RELEVANT_STATE": dict(relevant_state),
        "INPUT_REFERENCES": list(_tuple_strings(input_refs, limit=16, item_limit=160)),
        "OUTPUT_SCHEMA": safe_text(output_schema, 200),
    }
    safe_json(result, limit=budget)
    return result


def compact_context(items: Sequence[Mapping[str, Any]], budget: int = MAX_CONTEXT_CHARS) -> dict[str, Any]:
    """Keep decisions, state, outputs, failures, and evidence in a short summary."""
    keys = ("decisions", "constraints", "current_state", "completed_artifacts", "failed_methods", "open_tasks", "evidence")
    output: dict[str, Any] = {}
    for key in keys:
        values: list[Any] = []
        for item in items:
            value = item.get(key)
            if isinstance(value, list):
                values.extend(value)
            elif value not in (None, ""):
                values.append(value)
        if values:
            output[key] = values[:32]
    safe_json(output, limit=budget)
    return output


class TraceStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._events: list[dict[str, Any]] = []
        self._lock = RLock()

    def emit(self, event: str, **fields: Any) -> None:
        allowed = {
            "mission_id", "command_id", "parent_command_id", "agent_id", "parent_agent_id", "rank",
            "model", "task", "start_time", "end_time", "duration_ms", "tokens", "cache_hit",
            "children_spawned", "tools_used", "status", "error", "parallel_group", "stage",
        }
        safe_fields: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in {"model", "task", "error"}:
                safe_fields[key] = safe_text(value, 240)
            elif key == "tools_used" and isinstance(value, (list, tuple)):
                safe_fields[key] = list(_tuple_strings(value, 16, 80))
            elif isinstance(value, (str, int, float, bool)) or value is None:
                safe_fields[key] = value
        event_value = {"event": safe_text(event, 80), "timestamp": now_iso(), **safe_fields}
        safe_json(event_value, limit=4_000)
        with self._lock:
            self._events.append(event_value)
            if self.path:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event_value, ensure_ascii=False, sort_keys=True) + "\n")

    def events(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(self._events)


class ProgressEmitter:
    def __init__(self, trace: TraceStore) -> None:
        self.trace = trace

    def started(self, command: CommandEnvelope) -> None:
        self.trace.emit("started", mission_id=command.mission_id, command_id=command.command_id, parent_command_id=command.parent_command_id, agent_id=command.child_agent_id, parent_agent_id=command.parent_agent_id, rank=command.rank, status="running", parallel_group=command.parallel_group)

    def progress(self, command: CommandEnvelope, stage: str) -> None:
        self.trace.emit("progress", mission_id=command.mission_id, command_id=command.command_id, agent_id=command.child_agent_id, rank=command.rank, status="running", stage=stage)

    def completed(self, report: ReportEnvelope) -> None:
        self.trace.emit("completed", mission_id=report.mission_id, command_id=report.command_id, parent_command_id=report.parent_command_id, agent_id=report.agent_id, parent_agent_id=report.parent_agent_id, rank=report.rank, duration_ms=report.duration_ms, tokens=report.tokens_used, cache_hit=report.cache_hit, tools_used=report.tools_used, status=report.status)

    def failed(self, command: CommandEnvelope, error: str, status: str = "failed") -> None:
        self.trace.emit(status, mission_id=command.mission_id, command_id=command.command_id, agent_id=command.child_agent_id, parent_agent_id=command.parent_agent_id, rank=command.rank, status=status, error=error)


Handler = Callable[[CommandEnvelope], ReportEnvelope | Mapping[str, Any]]


class CommandRuntime:
    """Queue, idempotency, bounded fan-out/fan-in, cancellation, and metrics."""

    def __init__(self, registry: AgentRegistry | None = None, mission_budget: MissionBudget | None = None, artifacts: ArtifactStore | None = None, cache: HierarchicalCache | None = None, checkpoints: CheckpointStore | None = None, trace: TraceStore | None = None) -> None:
        self.registry = registry or AgentRegistry()
        self.ledger = BudgetLedger(mission_budget or MissionBudget())
        self.artifacts = artifacts or ArtifactStore()
        self.cache = cache or HierarchicalCache()
        self.checkpoints = checkpoints or CheckpointStore()
        self.trace = trace or TraceStore()
        self.progress = ProgressEmitter(self.trace)
        self._statuses: dict[str, str] = {}
        self._reports: dict[str, ReportEnvelope] = {}
        self._commands: dict[str, CommandEnvelope] = {}
        self._cancelled: set[str] = set()
        self._mission_cancel = Event()
        self._lock = RLock()
        self._duplicate_commands = 0
        self._reprocessed = 0
        self._start = time.monotonic()

    def is_cancelled(self, mission_id: str, command_id: str | None = None) -> bool:
        return self._mission_cancel.is_set() or (command_id is not None and command_id in self._cancelled)

    def cancel_mission(self, mission_id: str) -> None:
        with self._lock:
            self._mission_cancel.set()
            for command_id, command in self._commands.items():
                if command.mission_id == mission_id and self._statuses.get(command_id) in {"queued", "running"}:
                    self._cancelled.add(command_id)
                    self._statuses[command_id] = "cancelled"
                    self.progress.failed(command, "mission cancellation propagated", "cancelled")

    def status(self, command_id: str) -> str | None:
        with self._lock:
            return self._statuses.get(command_id)

    def dispatch(self, command: CommandEnvelope, handler: Handler) -> ReportEnvelope:
        command.validate(self.registry)
        with self._lock:
            if command.command_id in self._reports:
                self._duplicate_commands += 1
                return self._reports[command.command_id]
            if command.command_id in self._commands:
                self._duplicate_commands += 1
                existing = self._reports.get(command.command_id)
                if existing:
                    return existing
                raise RuntimeErrorBase("duplicate command is already running")
            if self.is_cancelled(command.mission_id, command.command_id):
                self._commands[command.command_id] = command
                self._statuses[command.command_id] = "cancelled"
                return self._blocked_report(command, "mission cancelled")
            for dependency in command.depends_on:
                if self._statuses.get(dependency) != "completed":
                    self._commands[command.command_id] = command
                    self._statuses[command.command_id] = "blocked"
                    return self._blocked_report(command, f"dependency not completed: {dependency}")
            self.ledger.reserve(command.token_budget, 1)
            self._commands[command.command_id] = command
            self._statuses[command.command_id] = "running"
        self.progress.started(command)
        started = time.monotonic()
        try:
            raw = handler(command)
            if isinstance(raw, ReportEnvelope):
                report = raw
            else:
                report = ReportEnvelope.from_dict(raw)
            duration = int((time.monotonic() - started) * 1000)
            if report.duration_ms == 0:
                report = ReportEnvelope(**{**report.to_dict(), "duration_ms": duration})
            report.validate(command, self.registry)
            self.ledger.settle(command.token_budget, 1, report.tokens_used, 1)
            with self._lock:
                self._reports[command.command_id] = report
                self._statuses[command.command_id] = report.status
            self.progress.completed(report)
            return report
        except Exception as exc:
            self.ledger.settle(command.token_budget, 1, 0, 1)
            message = safe_text(str(exc), 300) or "command failed"
            with self._lock:
                self._statuses[command.command_id] = "failed"
            self.progress.failed(command, message)
            return self._failed_report(command, message)

    def run_fan_out(self, commands: Sequence[CommandEnvelope], handlers: Mapping[str, Handler]) -> tuple[ReportEnvelope, ...]:
        if not commands:
            return ()
        parent_ids = {command.parent_agent_id for command in commands}
        if len(parent_ids) != 1:
            raise ContractError("fan-out commands must share one parent")
        parent = self.registry.get(next(iter(parent_ids)))
        if len(commands) > parent.max_children:
            raise ContractError("fan-out exceeds parent max_children")
        groups = {command.parallel_group for command in commands}
        if len(groups) != 1 or None in groups:
            raise ContractError("fan-out commands need one parallel_group")
        max_workers = min(parent.max_parallel, len(commands), MAX_PARALLEL)
        reports: list[ReportEnvelope] = []
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="agent-child") as pool:
            futures: dict[Future[ReportEnvelope], str] = {}
            for command in commands:
                handler = handlers.get(command.command_id)
                if handler is None:
                    raise ContractError(f"missing handler for {command.command_id}")
                futures[pool.submit(self.dispatch, command, handler)] = command.command_id
            for future in as_completed(futures):
                try:
                    reports.append(future.result())
                except Exception as exc:
                    command = next(item for item in commands if item.command_id == futures[future])
                    reports.append(self._failed_report(command, safe_text(str(exc), 300)))
        return tuple(sorted(reports, key=lambda report: report.command_id))

    def fan_in(self, parent_command: CommandEnvelope, child_reports: Sequence[ReportEnvelope]) -> ReportEnvelope:
        parent_command.validate(self.registry)
        if not child_reports:
            return self._failed_report(parent_command, "fan-in received no child reports")
        failed = [report for report in child_reports if report.status in {"failed", "blocked", "cancelled"}]
        status = "completed_with_warnings" if failed and len(failed) < len(child_reports) else ("failed" if failed else "completed")
        result = {
            "child_count": len(child_reports),
            "completed": sum(report.status == "completed" for report in child_reports),
            "failed": len(failed),
            "report_refs": [report.command_id for report in child_reports],
        }
        report = ReportEnvelope(
            mission_id=parent_command.mission_id, command_id=parent_command.command_id,
            parent_command_id=parent_command.parent_command_id, agent_id=parent_command.child_agent_id,
            parent_agent_id=parent_command.parent_agent_id, rank=parent_command.rank, status=status,
            summary=f"fan-in: {len(child_reports)} child reports; {len(failed)} partial failures",
            result=result, children_used=tuple(report.command_id for report in child_reports),
            warnings=tuple(report.summary for report in failed),
        )
        report.validate(parent_command, self.registry)
        with self._lock:
            self._reports[parent_command.command_id] = report
            self._statuses[parent_command.command_id] = report.status
        self.progress.completed(report)
        return report

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            reports = list(self._reports.values())
            commands = list(self._commands.values())
            status_values = list(self._statuses.values())
        durations = [report.duration_ms for report in reports]
        depths = [command.depth for command in commands]
        parallel = [command for command in commands if command.parallel_group]
        cache_hits = sum(report.cache_hit for report in reports)
        total_tokens = sum(report.tokens_used for report in reports)
        return {
            "total_tokens": total_tokens,
            "llm_api_calls": self.ledger.snapshot()["calls_used"],
            "mission_duration_ms": int((time.monotonic() - self._start) * 1000),
            "agent_count": len(commands),
            "average_agent_depth": sum(depths) / len(depths) if depths else 0.0,
            "average_children": len(commands) / max(1, len({command.parent_agent_id for command in commands})),
            "parallel_processing_rate": len(parallel) / len(commands) if commands else 0.0,
            "cache_hit_rate": cache_hits / len(reports) if reports else 0.0,
            "duplicate_commands": self._duplicate_commands,
            "reprocessed_commands": self._reprocessed,
            "tool_schema_tokens": 0,
            "context_tokens": 0,
            "failure_rate": sum(status in {"failed", "blocked", "cancelled"} for status in status_values) / len(status_values) if status_values else 0.0,
            "partial_failure_rate": sum(report.status == "completed_with_warnings" for report in reports) / len(reports) if reports else 0.0,
            "checkpoint_resume_rate": 0.0,
            "status_counts": {status: status_values.count(status) for status in COMMAND_STATUSES if status_values.count(status)},
            "budget": self.ledger.snapshot(),
            "cache": self.cache.stats(),
        }

    def _blocked_report(self, command: CommandEnvelope, reason: str) -> ReportEnvelope:
        report = ReportEnvelope(
            mission_id=command.mission_id, command_id=command.command_id, parent_command_id=command.parent_command_id,
            agent_id=command.child_agent_id, parent_agent_id=command.parent_agent_id, rank=command.rank,
            status="cancelled" if "cancel" in reason else "blocked", summary=reason, errors=(reason,),
        )
        with self._lock:
            self._reports[command.command_id] = report
        self.progress.failed(command, reason, report.status)
        return report

    def _failed_report(self, command: CommandEnvelope, reason: str) -> ReportEnvelope:
        report = ReportEnvelope(
            mission_id=command.mission_id, command_id=command.command_id, parent_command_id=command.parent_command_id,
            agent_id=command.child_agent_id, parent_agent_id=command.parent_agent_id, rank=command.rank,
            status="failed", summary=reason, errors=(reason,),
        )
        with self._lock:
            self._reports[command.command_id] = report
        return report


def make_command(registry: AgentRegistry, *, mission_id: str, command_id: str, parent_command_id: str | None, parent_agent_id: str, child_agent_id: str, mission: str, objective: str, constraints: Sequence[str], input_refs: Sequence[str], expected_output: Mapping[str, Any], token_budget: int, time_budget_ms: int, tool_scope: Sequence[str], done_when: Sequence[str], depth: int, inputs: Mapping[str, Any] | None = None, depends_on: Sequence[str] = (), parallel_group: str | None = None, priority: int = 0) -> CommandEnvelope:
    child = registry.get(child_agent_id)
    command = CommandEnvelope(
        mission_id=mission_id, command_id=command_id, parent_command_id=parent_command_id,
        parent_agent_id=parent_agent_id, child_agent_id=child_agent_id, owner_agent_id=child_agent_id,
        rank=child.rank, role=child.role, mission=safe_text(mission), objective=safe_text(objective),
        constraints=_tuple_strings(constraints, 16, 400), inputs=dict(inputs or {}), input_refs=_tuple_strings(input_refs, 16, 160),
        expected_output=dict(expected_output), token_budget=token_budget, time_budget_ms=time_budget_ms,
        tool_scope=_tuple_strings(tool_scope, 16, 100), may_spawn_children=child.may_spawn_children,
        allowed_child_roles=child.allowed_child_roles, depends_on=_tuple_strings(depends_on, 16, 128),
        parallel_group=safe_text(parallel_group, 80) or None, priority=priority, depth=depth,
        max_depth=min(MAX_DEPTH, child.max_depth), done_when=_tuple_strings(done_when, 16, 400),
        permissions=child.permissions, idempotency_key=command_id,
    )
    command.validate(registry)
    return command


__all__ = [
    "AgentRank", "AgentSpec", "AgentRegistry", "CommandEnvelope", "ReportEnvelope", "MissionBudget",
    "BudgetLedger", "ArtifactStore", "HierarchicalCache", "CheckpointStore", "TraceStore", "ProgressEmitter",
    "CommandRuntime", "ContractError", "PermissionError", "BudgetError", "default_agent_specs",
    "make_command", "project_context", "compact_context", "stable_hash", "stable_id",
]

