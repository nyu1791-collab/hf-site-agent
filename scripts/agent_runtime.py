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
from threading import RLock
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
    "cancelling",
    "completed",
    "completed_with_warnings",
    "blocked",
    "failed",
    "cancelled",
}
REPORT_STATUSES = COMMAND_STATUSES - {"queued", "running", "cancelling"}


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


READ_ONLY_OPERATION_TYPES = frozenset({"GET", "SEARCH", "READ", "PARSE"})


def is_read_only_operation(operation_type: str) -> bool:
    return isinstance(operation_type, str) and operation_type.strip().upper() in READ_ONLY_OPERATION_TYPES


def require_idempotency_key(idempotency_key: Any) -> str:
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ContractError("idempotency_key is required for side-effecting operations")
    return idempotency_key.strip()


def validate_operation_identity(operation_type: Any, command_id: Any, idempotency_key: Any = None) -> str:
    if not isinstance(operation_type, str) or not operation_type.strip():
        raise ContractError("operation_type is required")
    if not isinstance(command_id, str) or not command_id.strip():
        raise ContractError("command_id is required")
    normalized = operation_type.strip().upper()
    if not is_read_only_operation(normalized):
        require_idempotency_key(idempotency_key)
    return normalized


class IdempotencyConflict(ContractError):
    """The same key was reused for a different operation or payload."""


class IdempotencyInProgress(ContractError):
    """A prior attempt owns the key; retrying would risk a duplicate side effect."""


class IdempotencyStore:
    """Atomic idempotency records for side-effecting operations.

    Only the payload hash and a bounded, already-sanitized result are stored;
    raw request payloads, credentials, and provider error bodies are never
    written.  A non-completed record is deliberately non-retryable because a
    timeout can occur after an external side effect was accepted.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = RLock()
        if self.path and self.path.exists():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(loaded, Mapping) or not isinstance(loaded.get("records"), Mapping):
                raise ContractError("invalid idempotency ledger")
            self._records = {str(key): dict(value) for key, value in loaded["records"].items()}

    def _identity(
        self,
        *,
        mission_id: str,
        command_id: str,
        idempotency_key: str,
        operation_type: str,
        payload: Any,
    ) -> dict[str, str]:
        require_idempotency_key(idempotency_key)
        validate_operation_identity(operation_type, command_id, idempotency_key)
        if not ID_RE.fullmatch(mission_id) or not ID_RE.fullmatch(command_id):
            raise ContractError("invalid idempotency identity")
        operation = operation_type.strip()
        if len(operation) > 128:
            raise ContractError("operation_type is too long")
        return {
            "mission_id": mission_id,
            "command_id": command_id,
            "idempotency_key": idempotency_key.strip(),
            "operation_type": operation,
            "payload_hash": stable_hash(payload),
        }

    @staticmethod
    def _same_identity(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
        return all(left.get(key) == right.get(key) for key in (
            "mission_id", "command_id", "idempotency_key", "operation_type", "payload_hash",
        ))

    def _write_locked(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "records": self._records, "updated_at": now_iso()}
        safe_json(payload, limit=MAX_JSON_CHARS)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def claim(
        self,
        *,
        mission_id: str,
        command_id: str,
        idempotency_key: str,
        operation_type: str,
        payload: Any,
    ) -> dict[str, Any] | None:
        identity = self._identity(
            mission_id=mission_id, command_id=command_id, idempotency_key=idempotency_key,
            operation_type=operation_type, payload=payload,
        )
        key = identity["idempotency_key"]
        with self._lock:
            current = self._records.get(key)
            if current is not None:
                if not self._same_identity(current, identity):
                    raise IdempotencyConflict("IDEMPOTENCY_CONFLICT")
                if current.get("status") == "completed":
                    return dict(current)
                raise IdempotencyInProgress("IDEMPOTENCY_REPLAY_BLOCKED")
            self._records[key] = {
                **identity,
                "status": "in_progress",
                "result": None,
                "error_class": None,
                "created_at": now_iso(),
                "completed_at": None,
            }
            self._write_locked()
        return None

    def complete(
        self,
        *,
        mission_id: str,
        command_id: str,
        idempotency_key: str,
        operation_type: str,
        payload: Any,
        result: Any,
    ) -> dict[str, Any]:
        identity = self._identity(
            mission_id=mission_id, command_id=command_id, idempotency_key=idempotency_key,
            operation_type=operation_type, payload=payload,
        )
        safe_json(result)
        key = identity["idempotency_key"]
        with self._lock:
            current = self._records.get(key)
            if current is None or not self._same_identity(current, identity):
                raise IdempotencyConflict("IDEMPOTENCY_CONFLICT")
            if current.get("status") == "completed":
                return dict(current)
            current.update({"status": "completed", "result": result, "completed_at": now_iso()})
            self._write_locked()
            return dict(current)

    def execute(
        self,
        *,
        mission_id: str,
        command_id: str,
        idempotency_key: str,
        operation_type: str,
        payload: Any,
        operation: Callable[[], Any],
    ) -> Any:
        existing = self.claim(
            mission_id=mission_id, command_id=command_id, idempotency_key=idempotency_key,
            operation_type=operation_type, payload=payload,
        )
        if existing is not None:
            return existing["result"]
        try:
            result = operation()
        except Exception as exc:
            key = idempotency_key.strip()
            with self._lock:
                record = self._records.get(key)
                if record is not None:
                    record.update({
                        "status": "unknown" if isinstance(exc, (TimeoutError, ConnectionError)) else "failed",
                        "error_class": type(exc).__name__,
                        "completed_at": now_iso(),
                    })
                    self._write_locked()
            raise
        self.complete(
            mission_id=mission_id, command_id=command_id, idempotency_key=idempotency_key,
            operation_type=operation_type, payload=payload, result=result,
        )
        return result

    def get(self, idempotency_key: str) -> dict[str, Any] | None:
        key = require_idempotency_key(idempotency_key)
        with self._lock:
            record = self._records.get(key)
            return dict(record) if record is not None else None

    def records(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(dict(record) for record in self._records.values())


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
    capability_tags: tuple[str, ...] = ()
    required_features: tuple[str, ...] = ()
    provider_id: str | None = None
    model_binding_role: str | None = None
    active: bool = False
    requires_explicit_approval: bool = True

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
        if self.provider_id is not None and not ID_RE.fullmatch(self.provider_id):
            raise ContractError("invalid provider_id")
        if self.model_binding_role is not None and not ID_RE.fullmatch(self.model_binding_role):
            raise ContractError("invalid model_binding_role")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_agent_specs() -> tuple[AgentSpec, ...]:
    """Three provider commanders, bounded specialists, and OpenRouter workers."""

    families: tuple[tuple[str, str, str, str, str, tuple[str, ...], tuple[str, ...]], ...] = (
        (
            "google-general-commander", "GENERAL_COMMANDER", "google", "総合・研究・企画・情報統合軍",
            "research planning long_context multimodal synthesis",
            ("research", "data", "media", "long-context", "fact-check", "planning", "product", "content"),
            ("public_web_read", "artifact_read", "artifact_write", "queue", "trace"),
        ),
        (
            "nvidia-engineering-commander", "ENGINEERING_COMMANDER", "nvidia", "技術・開発・工兵軍",
            "repository coding debug testing infrastructure tools",
            ("repository", "coding", "backend", "frontend", "test", "debug", "security-review", "infrastructure", "automation", "video", "code", "qa", "metrics"),
            ("repo_read", "tests_read", "artifact_read", "artifact_write", "colab_plan", "queue", "trace"),
        ),
        (
            "groq-rapid-commander", "RAPID_EXECUTION_COMMANDER", "groq", "高速即応・前処理・大量処理軍",
            "fast summary classification extraction json batch",
            ("fast-summary", "fast-classifier", "fast-json", "log-analysis", "text-normalizer", "light-coding", "first-pass-review"),
            ("filesystem_read", "artifact_read", "artifact_write", "queue", "trace"),
        ),
    )
    specs: list[AgentSpec] = [
        AgentSpec(
            "chatgpt-work", None, AgentRank.COMMANDER, "commander",
            "全体Missionの解釈・承認・統合・最終実行判断",
            allowed_children=tuple(item[0] for item in families),
            allowed_tools=(
                "approval", "github_read", "github_write", "public_web_read", "repo_read", "tests_read",
                "filesystem_read", "colab_plan", "artifact_read", "artifact_write", "queue",
                "model:role-registry", "trace",
            ),
            working_directory="artifacts/commander", context_budget=8_000, token_budget=8_000,
            time_budget_ms=300_000, max_children=3, max_parallel=3, max_depth=MAX_DEPTH,
            permissions=(
                "approve", "pr", "deploy_after_review", "publish_after_review", "propose", "decompose",
                "draft", "report", "execute_bounded_unit",
            ),
            report_schema="report-envelope-v1", may_spawn_children=True,
            allowed_child_roles=("general_commander", "engineering_commander", "rapid_execution_commander"),
            capability_tags=("orchestration", "approval", "integration"),
        )
    ]
    for agent_id, role, provider_id, mission, capabilities, specialist_roles, commander_tools in families:
        specs.append(
            AgentSpec(
                agent_id, "chatgpt-work", AgentRank.UPPER_COMMANDER, role.lower(), mission,
                allowed_children=tuple(f"{name}-specialist" for name in specialist_roles),
                allowed_tools=commander_tools + ("model:role-registry",),
                working_directory=f"artifacts/{provider_id}/commander", context_budget=6_000, token_budget=1_000,
                time_budget_ms=30_000, max_children=MAX_CHILDREN, max_parallel=4, max_depth=MAX_DEPTH,
                permissions=("propose", "decompose", "draft", "report", "execute_bounded_unit"), report_schema="report-envelope-v1",
                may_spawn_children=True, allowed_child_roles=("specialist_commander",),
                capability_tags=tuple(capabilities.split()), required_features=("structured_output", "tool_calling"),
                provider_id=provider_id,
                model_binding_role=f"ROLE_{provider_id.upper()}_{role}", active=False,
                requires_explicit_approval=True,
            )
        )
        for specialist_role in specialist_roles:
            specialist_id = f"{specialist_role}-specialist"
            worker_id = f"{specialist_role}-worker"
            specialist_tools = commander_tools + (("colab_plan",) if specialist_role == "video" else ())
            specs.append(
                AgentSpec(
                    specialist_id, agent_id, AgentRank.SPECIALIST_COMMANDER, "specialist_commander",
                    f"{specialist_role}領域の承認済みTaskを下書き化して親へ報告",
                    allowed_children=(worker_id,), allowed_tools=specialist_tools,
                    working_directory=f"artifacts/{provider_id}/{specialist_role}", context_budget=4_000, token_budget=500,
                    time_budget_ms=15_000, max_children=1, max_parallel=1, max_depth=MAX_DEPTH,
                    permissions=("draft", "report", "execute_bounded_unit"), report_schema="report-envelope-v1",
                    may_spawn_children=True, allowed_child_roles=("worker",),
                    capability_tags=(specialist_role,), required_features=("structured_output",),
                    provider_id=provider_id, model_binding_role=f"ROLE_{provider_id.upper()}_SPECIALIST",
                    active=False, requires_explicit_approval=True,
                )
            )
            specs.append(
                AgentSpec(
                    worker_id, specialist_id, AgentRank.WORKER, "worker",
                    f"{specialist_role}の小さな通常コード・参照処理",
                    allowed_children=(), allowed_tools=("artifact_read", "artifact_write", "trace"),
                    working_directory=f"artifacts/{provider_id}/{specialist_role}/worker", context_budget=2_000, token_budget=200,
                    time_budget_ms=10_000, max_children=0, max_parallel=1, max_depth=MAX_DEPTH,
                    permissions=("execute_bounded_unit", "report"), report_schema="report-envelope-v1",
                    may_spawn_children=False, allowed_child_roles=(), capability_tags=("worker", specialist_role),
                    required_features=(), provider_id="openrouter", model_binding_role="ROLE_OPENROUTER_WORKER",
                    active=False, requires_explicit_approval=True,
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
                if not set(spec.allowed_tools).issubset(set(parent.allowed_tools)):
                    raise PermissionError(f"child tools exceed parent scope: {parent.agent_id} -> {spec.agent_id}")
                if not set(spec.permissions).issubset(set(parent.permissions)):
                    raise PermissionError(f"child permissions exceed parent scope: {parent.agent_id} -> {spec.agent_id}")
                # Provider commanders are sibling upper-command agents under
                # ChatGPT Work. Equal rank is valid; only direct
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
    artifact_refs: tuple[str, ...] = ()
    expected_output: Mapping[str, Any] = field(default_factory=dict)
    provider_preference: str | None = None
    deadline: str | None = None
    token_budget: int = 1
    time_budget_ms: int = 1_000
    request_budget: int = 0
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
    estimated_free_requests: int = 0
    idempotency_key: str = ""
    side_effect_level: str = "read_only_draft"
    created_at: str = field(default_factory=now_iso)
    status: str = "queued"

    def validate(self, registry: AgentRegistry) -> None:
        for label, value in (("mission_id", self.mission_id), ("command_id", self.command_id), ("parent_agent_id", self.parent_agent_id), ("child_agent_id", self.child_agent_id), ("owner_agent_id", self.owner_agent_id)):
            if not ID_RE.fullmatch(value):
                raise ContractError(f"invalid {label}")
        if self.parent_command_id is not None and not ID_RE.fullmatch(self.parent_command_id):
            raise ContractError("invalid parent_command_id")
        if self.provider_preference is not None and not ID_RE.fullmatch(self.provider_preference):
            raise ContractError("invalid provider_preference")
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
        if self.request_budget < 0 or self.request_budget > 1_000:
            raise BudgetError("request_budget is outside bounds")
        if self.estimated_free_requests < 0 or self.estimated_free_requests > 1_000:
            raise BudgetError("estimated_free_requests is outside bounds")
        if self.side_effect_level not in {"read_only", "read_only_draft", "dry_run", "mutation"}:
            raise ContractError("unknown side_effect_level")
        require_idempotency_key(self.idempotency_key)
        safe_json(dict(self.inputs), limit=MAX_JSON_CHARS)
        safe_json({"input_refs": self.input_refs, "artifact_refs": self.artifact_refs}, limit=MAX_JSON_CHARS)
        safe_json(dict(self.expected_output), limit=MAX_JSON_CHARS)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["inputs"] = dict(self.inputs)
        value["expected_output"] = dict(self.expected_output)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CommandEnvelope":
        data = dict(value)
        for key in ("constraints", "input_refs", "artifact_refs", "tool_scope", "allowed_child_roles", "depends_on", "done_when", "permissions"):
            data[key] = _tuple_strings(data.get(key))
        if "artifact_refs" not in value:
            data["artifact_refs"] = data["input_refs"]
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
    provider: str = ""
    model: str = ""
    result: Mapping[str, Any] = field(default_factory=dict)
    artifacts: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    children_used: tuple[str, ...] = ()
    duration_ms: int = 0
    tokens_used: int = 0
    requests_used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    quota_state: str = "UNKNOWN"
    cost: float | None = None
    error_class: str | None = None
    free_requests_used: int = 0
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
        if self.provider and not ID_RE.fullmatch(self.provider):
            raise ContractError("invalid report provider")
        if len(self.model) > 160 or any(ord(char) < 32 for char in self.model):
            raise ContractError("invalid report model")
        if not set(self.tools_used).issubset(set(child.allowed_tools)):
            raise PermissionError("report tool list exceeds child allowance")
        if not 0 <= self.duration_ms <= command.time_budget_ms:
            raise BudgetError("report duration exceeds command budget")
        if not 0 <= self.tokens_used <= command.token_budget:
            raise BudgetError("report tokens exceed command budget")
        request_limit = max(command.request_budget, command.estimated_free_requests)
        if self.free_requests_used < 0 or self.free_requests_used > request_limit + 1:
            raise BudgetError("report free request count exceeds command estimate")
        if self.requests_used < 0 or self.input_tokens < 0 or self.output_tokens < 0:
            raise BudgetError("report usage counters cannot be negative")
        if self.requests_used > request_limit + 1:
            raise BudgetError("report requests exceed command budget")
        if self.cost is not None and self.cost < 0:
            raise ContractError("report cost cannot be negative")
        if len(self.quota_state) > 80 or any(ord(char) < 32 for char in self.quota_state):
            raise ContractError("invalid report quota state")
        if self.error_class is not None and (len(self.error_class) > 120 or any(ord(char) < 32 for char in self.error_class)):
            raise ContractError("invalid report error class")
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
    max_total_tokens: int | None = None
    max_google_requests: int | None = None
    max_nvidia_requests: int | None = None
    max_groq_requests: int | None = None
    max_openrouter_requests: int | None = None
    max_parallel: int = MAX_PARALLEL
    deadline: str | None = None

    def validate(self) -> None:
        if self.token_budget < 1 or self.call_budget < 1 or self.time_budget_ms < 1:
            raise BudgetError("mission budget must be positive")
        if not 1 <= self.max_depth <= MAX_DEPTH:
            raise BudgetError("mission max_depth is outside bounds")
        if self.max_total_tokens is not None and self.max_total_tokens < 1:
            raise BudgetError("max_total_tokens must be positive")
        for field_name in (
            "max_google_requests", "max_nvidia_requests", "max_groq_requests", "max_openrouter_requests",
        ):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise BudgetError(f"{field_name} cannot be negative")
        if not 1 <= self.max_parallel <= MAX_PARALLEL:
            raise BudgetError("mission max_parallel is outside bounds")
        if self.deadline is not None and (not isinstance(self.deadline, str) or len(self.deadline) > 80):
            raise BudgetError("mission deadline is invalid")

    def provider_limit(self, provider_id: str) -> int | None:
        return {
            "google": self.max_google_requests,
            "nvidia": self.max_nvidia_requests,
            "groq": self.max_groq_requests,
            "openrouter": self.max_openrouter_requests,
        }.get(provider_id)


class BudgetLedger:
    def __init__(self, budget: MissionBudget) -> None:
        budget.validate()
        self.budget = budget
        self._token_limit = min(budget.token_budget, budget.max_total_tokens or budget.token_budget)
        self._reserved_tokens = 0
        self._reserved_calls = 0
        self._used_tokens = 0
        self._used_calls = 0
        self._reserved_provider_calls: dict[str, int] = {}
        self._used_provider_calls: dict[str, int] = {}
        self._lock = RLock()

    def reserve(self, tokens: int, calls: int = 1, provider_id: str | None = None, provider_calls: int | None = None) -> None:
        with self._lock:
            if tokens < 0 or calls < 0:
                raise BudgetError("negative budget reservation")
            if self._used_tokens + self._reserved_tokens + tokens > self._token_limit:
                raise BudgetError("mission token budget exhausted")
            if self._used_calls + self._reserved_calls + calls > self.budget.call_budget:
                raise BudgetError("mission call budget exhausted")
            if provider_id is not None:
                limit = self.budget.provider_limit(provider_id)
                if limit is None:
                    raise BudgetError(f"provider request budget is not configured: {provider_id}")
                provider_request_count = calls if provider_calls is None else provider_calls
                if provider_request_count < 1:
                    raise BudgetError("provider request reservation must be positive")
                current = self._used_provider_calls.get(provider_id, 0) + self._reserved_provider_calls.get(provider_id, 0)
                if current + provider_request_count > limit:
                    raise BudgetError(f"provider request budget exhausted: {provider_id}")
                self._reserved_provider_calls[provider_id] = self._reserved_provider_calls.get(provider_id, 0) + provider_request_count
            self._reserved_tokens += tokens
            self._reserved_calls += calls

    def settle(self, reserved_tokens: int, reserved_calls: int, used_tokens: int, used_calls: int = 1, provider_id: str | None = None, provider_reserved_calls: int = 0, provider_used_calls: int | None = None) -> None:
        with self._lock:
            self._reserved_tokens = max(0, self._reserved_tokens - reserved_tokens)
            self._reserved_calls = max(0, self._reserved_calls - reserved_calls)
            self._used_tokens += max(0, used_tokens)
            self._used_calls += max(0, used_calls)
            if provider_id is not None:
                reserved = max(0, provider_reserved_calls)
                self._reserved_provider_calls[provider_id] = max(0, self._reserved_provider_calls.get(provider_id, 0) - reserved)
                consumed = reserved if provider_used_calls is None else max(0, provider_used_calls)
                self._used_provider_calls[provider_id] = self._used_provider_calls.get(provider_id, 0) + consumed
            if self._used_tokens > self._token_limit or self._used_calls > self.budget.call_budget:
                raise BudgetError("mission budget exceeded")

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "token_budget": self.budget.token_budget,
                "max_total_tokens": self._token_limit,
                "call_budget": self.budget.call_budget,
                "time_budget_ms": self.budget.time_budget_ms,
                "tokens_used": self._used_tokens,
                "calls_used": self._used_calls,
                "tokens_reserved": self._reserved_tokens,
                "calls_reserved": self._reserved_calls,
                "provider_requests_used": dict(self._used_provider_calls),
                "provider_requests_reserved": dict(self._reserved_provider_calls),
                "provider_request_budgets": {
                    provider: self.budget.provider_limit(provider)
                    for provider in ("google", "nvidia", "groq", "openrouter")
                },
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
            cached = self._items.get(key)
            if cached is not None:
                return dict(cached)
            if not self.root:
                return None
            path = self.root / f"{stable_hash(key)[:24]}.json"
            if not path.exists():
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise ContractError("checkpoint is invalid") from exc
            if not isinstance(payload, Mapping) or payload.get("mission_id") != mission_id or payload.get("stage") != stage:
                raise ContractError("checkpoint identity does not match")
            safe_json(payload)
            self._items[key] = dict(payload)
            return dict(payload)


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

    def __init__(self, registry: AgentRegistry | None = None, mission_budget: MissionBudget | None = None, artifacts: ArtifactStore | None = None, cache: HierarchicalCache | None = None, checkpoints: CheckpointStore | None = None, trace: TraceStore | None = None, idempotency: IdempotencyStore | None = None) -> None:
        self.registry = registry or AgentRegistry()
        self.ledger = BudgetLedger(mission_budget or MissionBudget())
        self.artifacts = artifacts or ArtifactStore()
        self.cache = cache or HierarchicalCache()
        self.checkpoints = checkpoints or CheckpointStore()
        self.trace = trace or TraceStore()
        self.idempotency = idempotency or IdempotencyStore()
        self.progress = ProgressEmitter(self.trace)
        self._statuses: dict[str, str] = {}
        self._reports: dict[str, ReportEnvelope] = {}
        self._commands: dict[str, CommandEnvelope] = {}
        # Command cancellation is mission-qualified.  A command id is only
        # unique within its mission boundary, and a caller must never be able
        # to cancel a same-named command from another mission.
        self._cancelled: set[tuple[str, str]] = set()
        self._cancelled_missions: set[str] = set()
        self._lock = RLock()
        self._duplicate_commands = 0
        self._reprocessed = 0
        self._start = time.monotonic()

    def _is_cancelled_locked(
        self,
        mission_id: str,
        command_id: str | None = None,
        parent_command_id: str | None = None,
    ) -> bool:
        if mission_id in self._cancelled_missions:
            return True
        current = command_id
        visited: set[str] = set()
        while current is not None and current not in visited:
            if (mission_id, current) in self._cancelled:
                return True
            visited.add(current)
            known = self._commands.get(current)
            if known is None or known.mission_id != mission_id:
                current = None
            else:
                current = known.parent_command_id
        if parent_command_id and parent_command_id not in visited:
            return (mission_id, parent_command_id) in self._cancelled
        return False

    def is_cancelled(
        self,
        mission_id: str,
        command_id: str | None = None,
        parent_command_id: str | None = None,
    ) -> bool:
        with self._lock:
            return self._is_cancelled_locked(mission_id, command_id, parent_command_id)

    def _is_descendant_locked(self, command_id: str, ancestor_command_id: str) -> bool:
        current = command_id
        visited: set[str] = set()
        while current not in visited:
            if current == ancestor_command_id:
                return True
            visited.add(current)
            known = self._commands.get(current)
            if known is None or known.parent_command_id is None:
                return False
            current = known.parent_command_id
        return False

    def cancel_mission(self, mission_id: str) -> None:
        """Cancel only commands belonging to one mission.

        A running handler is marked ``cancelling`` first.  Dispatch finalizes
        it as ``cancelled`` when the handler returns; handlers that perform
        cooperative checks can stop early through ``is_cancelled``.  Completed
        and failed results are never overwritten.
        """
        queued: list[CommandEnvelope] = []
        running: list[CommandEnvelope] = []
        with self._lock:
            self._cancelled_missions.add(mission_id)
            for command_id, command in self._commands.items():
                if command.mission_id != mission_id:
                    continue
                status = self._statuses.get(command_id)
                if status == "queued":
                    self._cancelled.add((mission_id, command_id))
                    self._statuses[command_id] = "cancelled"
                    queued.append(command)
                elif status == "running":
                    self._cancelled.add((mission_id, command_id))
                    self._statuses[command_id] = "cancelling"
                    running.append(command)
        for command in queued:
            self._blocked_report(command, "mission cancellation propagated")
        for command in running:
            self.progress.failed(command, "mission cancellation requested", "cancelling")

    def cancel_command(self, command_id: str) -> None:
        """Cancel one command and its descendants, without touching siblings."""
        queued: list[CommandEnvelope] = []
        running: list[CommandEnvelope] = []
        with self._lock:
            target = self._commands.get(command_id)
            if target is None:
                raise ContractError(f"unknown command: {command_id}")
            self._cancelled.add((target.mission_id, command_id))
            for candidate_id, command in self._commands.items():
                if command.mission_id != target.mission_id:
                    continue
                if not self._is_descendant_locked(candidate_id, command_id):
                    continue
                status = self._statuses.get(candidate_id)
                if status == "queued":
                    self._cancelled.add((target.mission_id, candidate_id))
                    self._statuses[candidate_id] = "cancelled"
                    queued.append(command)
                elif status == "running":
                    self._cancelled.add((target.mission_id, candidate_id))
                    self._statuses[candidate_id] = "cancelling"
                    running.append(command)
        for command in queued:
            self._blocked_report(command, "command cancellation propagated")
        for command in running:
            self.progress.failed(command, "command cancellation requested", "cancelling")

    def status(self, command_id: str) -> str | None:
        with self._lock:
            return self._statuses.get(command_id)

    @staticmethod
    def _command_payload(command: CommandEnvelope) -> dict[str, Any]:
        value = command.to_dict()
        # These fields describe the envelope instance, not the requested work.
        # Excluding them makes a retry hash stable across process restarts.
        value.pop("idempotency_key", None)
        value.pop("created_at", None)
        value.pop("status", None)
        return value

    def dispatch(self, command: CommandEnvelope, handler: Handler) -> ReportEnvelope:
        command.validate(self.registry)
        payload = self._command_payload(command)
        provider_id = command.provider_preference
        provider_reserved_calls = (
            max(1, command.request_budget, command.estimated_free_requests)
            if provider_id is not None else 0
        )
        idempotency_claimed = False
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
            replay = self.idempotency.claim(
                mission_id=command.mission_id,
                command_id=command.command_id,
                idempotency_key=command.idempotency_key,
                operation_type="agent_dispatch",
                payload=payload,
            )
            idempotency_claimed = replay is None
            if replay is not None:
                replay_report = ReportEnvelope.from_dict(replay.get("result") or {})
                replay_report.validate(command, self.registry)
                self._reports[command.command_id] = replay_report
                self._statuses[command.command_id] = replay_report.status
                self._duplicate_commands += 1
                return replay_report
            if self.is_cancelled(command.mission_id, command.command_id, command.parent_command_id):
                self._commands[command.command_id] = command
                self._cancelled.add((command.mission_id, command.command_id))
                self._statuses[command.command_id] = "cancelled"
                report = self._blocked_report(command, "mission cancelled")
                self.idempotency.complete(
                    mission_id=command.mission_id, command_id=command.command_id,
                    idempotency_key=command.idempotency_key, operation_type="agent_dispatch",
                    payload=payload, result=report.to_dict(),
                )
                return report
            for dependency in command.depends_on:
                if self._statuses.get(dependency) != "completed":
                    self._commands[command.command_id] = command
                    self._statuses[command.command_id] = "blocked"
                    report = self._blocked_report(command, f"dependency not completed: {dependency}")
                    self.idempotency.complete(
                        mission_id=command.mission_id, command_id=command.command_id,
                        idempotency_key=command.idempotency_key, operation_type="agent_dispatch",
                        payload=payload, result=report.to_dict(),
                    )
                    return report
            self.ledger.reserve(
                command.token_budget,
                1,
                provider_id=provider_id,
                provider_calls=provider_reserved_calls or None,
            )
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
            with self._lock:
                cancellation_requested = self._is_cancelled_locked(
                    command.mission_id, command.command_id, command.parent_command_id
                )
            if cancellation_requested:
                self.ledger.settle(
                    command.token_budget, 1, 0, 1,
                    provider_id=provider_id,
                    provider_reserved_calls=provider_reserved_calls,
                    provider_used_calls=provider_reserved_calls or None,
                )
                report = self._blocked_report(command, "mission cancellation propagated")
                self.idempotency.complete(
                    mission_id=command.mission_id, command_id=command.command_id,
                    idempotency_key=command.idempotency_key, operation_type="agent_dispatch",
                    payload=payload, result=report.to_dict(),
                )
                return report
            report.validate(command, self.registry)
            with self._lock:
                # Cancellation wins only while the command is still running or
                # cancelling.  A completed result is never overwritten.
                if self._is_cancelled_locked(command.mission_id, command.command_id, command.parent_command_id):
                    cancellation_requested = True
                else:
                    self._reports[command.command_id] = report
                    self._statuses[command.command_id] = report.status
            self.ledger.settle(
                command.token_budget, 1, 0 if cancellation_requested else report.tokens_used, 1,
                provider_id=provider_id,
                provider_reserved_calls=provider_reserved_calls,
                provider_used_calls=provider_reserved_calls or None,
            )
            if cancellation_requested:
                cancelled_report = self._blocked_report(command, "mission cancellation propagated")
                self.idempotency.complete(
                    mission_id=command.mission_id, command_id=command.command_id,
                    idempotency_key=command.idempotency_key, operation_type="agent_dispatch",
                    payload=payload, result=cancelled_report.to_dict(),
                )
                return cancelled_report
            self.idempotency.complete(
                mission_id=command.mission_id, command_id=command.command_id,
                idempotency_key=command.idempotency_key, operation_type="agent_dispatch",
                payload=payload, result=report.to_dict(),
            )
            self.progress.completed(report)
            return report
        except Exception as exc:
            self.ledger.settle(
                command.token_budget, 1, 0, 1,
                provider_id=provider_id,
                provider_reserved_calls=provider_reserved_calls,
                provider_used_calls=provider_reserved_calls or None,
            )
            message = safe_text(str(exc), 300) or "command failed"
            with self._lock:
                self._statuses[command.command_id] = "failed"
            self.progress.failed(command, message)
            report = self._failed_report(command, message)
            if idempotency_claimed:
                self.idempotency.complete(
                    mission_id=command.mission_id, command_id=command.command_id,
                    idempotency_key=command.idempotency_key, operation_type="agent_dispatch",
                    payload=payload, result=report.to_dict(),
                )
            return report

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
        max_workers = min(parent.max_parallel, self.ledger.budget.max_parallel, len(commands), MAX_PARALLEL)
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
            if command.command_id in self._reports:
                return self._reports[command.command_id]
            self._reports[command.command_id] = report
            self._statuses[command.command_id] = report.status
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


def make_command(registry: AgentRegistry, *, mission_id: str, command_id: str, parent_command_id: str | None, parent_agent_id: str, child_agent_id: str, mission: str, objective: str, constraints: Sequence[str], input_refs: Sequence[str], expected_output: Mapping[str, Any], token_budget: int, time_budget_ms: int, tool_scope: Sequence[str], done_when: Sequence[str], depth: int, inputs: Mapping[str, Any] | None = None, depends_on: Sequence[str] = (), parallel_group: str | None = None, priority: int = 0, estimated_free_requests: int = 0, request_budget: int | None = None, provider_preference: str | None = None, side_effect_level: str = "read_only_draft", deadline: str | None = None) -> CommandEnvelope:
    child = registry.get(child_agent_id)
    command = CommandEnvelope(
        mission_id=mission_id, command_id=command_id, parent_command_id=parent_command_id,
        parent_agent_id=parent_agent_id, child_agent_id=child_agent_id, owner_agent_id=child_agent_id,
        rank=child.rank, role=child.role, mission=safe_text(mission), objective=safe_text(objective),
        constraints=_tuple_strings(constraints, 16, 400), inputs=dict(inputs or {}), input_refs=_tuple_strings(input_refs, 16, 160),
        artifact_refs=_tuple_strings(input_refs, 16, 160), expected_output=dict(expected_output),
        provider_preference=provider_preference, deadline=deadline, token_budget=token_budget, time_budget_ms=time_budget_ms,
        request_budget=estimated_free_requests if request_budget is None else request_budget,
        tool_scope=_tuple_strings(tool_scope, 16, 100), may_spawn_children=child.may_spawn_children,
        allowed_child_roles=child.allowed_child_roles, depends_on=_tuple_strings(depends_on, 16, 128),
        parallel_group=safe_text(parallel_group, 80) or None, priority=priority, depth=depth,
        max_depth=min(MAX_DEPTH, child.max_depth), done_when=_tuple_strings(done_when, 16, 400),
        permissions=child.permissions, estimated_free_requests=estimated_free_requests, idempotency_key=command_id,
        side_effect_level=side_effect_level,
    )
    command.validate(registry)
    return command


__all__ = [
    "AgentRank", "AgentSpec", "AgentRegistry", "CommandEnvelope", "ReportEnvelope", "MissionBudget",
    "BudgetLedger", "ArtifactStore", "HierarchicalCache", "CheckpointStore", "TraceStore", "ProgressEmitter",
    "CommandRuntime", "ContractError", "PermissionError", "BudgetError", "IdempotencyConflict",
    "IdempotencyInProgress", "IdempotencyStore", "default_agent_specs", "make_command",
    "project_context", "compact_context", "stable_hash", "stable_id", "is_read_only_operation",
    "require_idempotency_key", "validate_operation_identity",
]
