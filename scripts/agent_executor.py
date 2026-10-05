#!/usr/bin/env python3
"""Run one commander-approved specialist task as a draft-only agent.

This worker may create a research, content, video, code, QA, metrics, or
product artifact and propose additional tasks.  It cannot write repositories,
deploy, publish, change secrets, spend money, or call external publishing
APIs.  The packet is returned to the commander for a separate review.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts.model_registry import load_registry, role_config
    from scripts.free_quota import FreeQuotaBlocked, FreeUsageLedger, is_explicit_free_model
    from scripts.agent_runtime import AgentRegistry, ReportEnvelope, make_command, project_context, stable_hash, stable_id
    from scripts.provider_adapters import GuardedProviderAdapter, OpenAICompatibleAdapter, ProviderAdapterError
    from scripts.provider_controls import QuotaGuardError, ledger_from_registry
    from scripts.provider_registry import load_provider_registry, provider_config
    from scripts.worker_selection import worker_role_for_specialist
except ModuleNotFoundError:  # pragma: no cover - when invoked from scripts/
    from model_registry import load_registry, role_config
    from free_quota import FreeQuotaBlocked, FreeUsageLedger, is_explicit_free_model
    from agent_runtime import AgentRegistry, ReportEnvelope, make_command, project_context, stable_hash, stable_id
    from provider_adapters import GuardedProviderAdapter, OpenAICompatibleAdapter, ProviderAdapterError
    from provider_controls import QuotaGuardError, ledger_from_registry
    from provider_registry import load_provider_registry, provider_config
    from worker_selection import worker_role_for_specialist


ROLE_TO_SPECIALIST = {
    "research": "research-specialist",
    "data": "data-specialist",
    "media": "media-specialist",
    "long_context": "long-context-specialist",
    "long-context": "long-context-specialist",
    "fact_check": "fact-check-specialist",
    "fact-check": "fact-check-specialist",
    "planning": "planning-specialist",
    "product": "product-specialist",
    "content": "content-specialist",
    "video": "video-specialist",
    "code": "code-specialist",
    "qa": "qa-specialist",
    "metrics": "metrics-specialist",
    "specialist": "product-specialist",
    "specialist_commander": "product-specialist",
}

PARENT_ROLE_BY_AGENT = {
    "google-general-commander": "ROLE_GOOGLE_GENERAL_COMMANDER",
    "nvidia-engineering-commander": "ROLE_NVIDIA_ENGINEERING_COMMANDER",
    "groq-rapid-commander": "ROLE_GROQ_RAPID_EXECUTION_COMMANDER",
}

MAX_INSTRUCTION = 5000
MAX_CONTEXT = 6000
MAX_ACCEPTANCE = 3000
MAX_OUTPUT_CHARS = 7000
MAX_MODEL_CHARS = 160
MAX_TOKENS = 500
TIMEOUT_SECONDS = 15.0
MAX_NEXT_TASKS = 4
MODEL_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,160}$")
FREE_MODEL_RE = re.compile(r"^[A-Za-z0-9._/-]+:free$")
SECRET_PATTERNS = (
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)(?:sk|gsk|hf|sk-or-v1)[_-][A-Za-z0-9_-]{12,}"),
    re.compile(
        r"(?i)\b(?:AI_API_KEY|GROQ_API_KEY|HF_TOKEN|HF_SPACE_WRITE_TOKEN|"
        r"GITHUB_TOKEN|WORKER_ADMIN_PASSWORD|CLOUDFLARE_API_TOKEN)\b"
    ),
)


def redact(value: str) -> str:
    result = value
    for pattern in SECRET_PATTERNS:
        result = pattern.sub("[redacted]", result)
    return result


def fail(message: str, status: int | None = None) -> None:
    payload: dict[str, Any] = {"ok": False, "error": redact(message)}
    if status is not None:
        payload["status"] = status
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    raise SystemExit(1)


def clean(value: Any, limit: int) -> str:
    if isinstance(value, str):
        text = value
    elif value is None:
        text = ""
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return redact(text.strip())[:limit]


def clean_list(value: Any, limit: int = 8, item_limit: int = 500) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (clean(entry, item_limit) for entry in value[:limit]) if item]


def specialist_for_role(role: str) -> str:
    key = clean(role, 80).lower().replace("-", "_")
    if key in ROLE_TO_SPECIALIST:
        return ROLE_TO_SPECIALIST[key]
    for role_name, agent_id in ROLE_TO_SPECIALIST.items():
        if role_name in key:
            return agent_id
    return "product-specialist"


def bounded_identifier(value: str, fallback: str) -> str:
    clean_value = re.sub(r"[^A-Za-z0-9._:-]+", "-", clean(value, 120)).strip("-")
    return (clean_value or fallback)[:128]


def parse_output(response: Any) -> dict[str, Any]:
    if isinstance(response, Mapping):
        raw = response.get("text", "")
    else:
        choices = getattr(response, "choices", None) or []
        if not choices:
            fail("Agent returned no choices.")
        message = getattr(choices[0], "message", None)
        raw = getattr(message, "content", "") if message is not None else ""
    safe = redact(str(raw or ""))[:MAX_OUTPUT_CHARS]
    if not safe:
        fail("Agent returned an empty response.")
    candidate = safe.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.IGNORECASE)
    try:
        parsed = json.loads(candidate)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"text": safe, "format": "text"}
    return parsed if isinstance(parsed, dict) else {"text": safe, "format": "text"}


def load_verified_worker_model(role: str, requested_model: str = "") -> str:
    """Resolve one exact worker from a separately generated probe report."""
    probe_path_value = os.environ.get("WORKER_PROBE_PATH", "artifacts/free_worker_probe.json").strip()
    probe_path = Path(probe_path_value)
    if probe_path.is_absolute() or ".." in probe_path.parts:
        fail("Worker probe path must stay inside the workspace.")
    try:
        report = json.loads(probe_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        fail("A current exact Worker probe report is required.")
    if not isinstance(report, Mapping) or report.get("registry_changed") is not False:
        fail("Worker probe report is invalid or changed the registry.")
    if report.get("provider_allow_fallbacks") is not False or report.get("paid_fallback") is not False:
        fail("Worker probe did not prove the no-fallback free policy.")
    worker_role = worker_role_for_specialist(role)
    selections = report.get("selections")
    selection = selections.get(worker_role) if isinstance(selections, Mapping) else None
    model = str(selection.get("model") or "").strip() if isinstance(selection, Mapping) else ""
    if not isinstance(selection, Mapping) or selection.get("status") != "ready" or selection.get("provider") != "openrouter":
        fail("No verified free Worker is available for this specialist role.")
    if selection.get("generic_router") is True or selection.get("paid_fallback") is not False:
        fail("The selected Worker is outside the free Worker policy.")
    if requested_model and requested_model != model:
        fail("The requested Worker model does not match the current exact probe.")
    if not MODEL_RE.fullmatch(model) or not FREE_MODEL_RE.fullmatch(model):
        fail("The exact Worker probe returned an invalid model ID.")
    results = report.get("results")
    evidence = next(
        (item for item in results if isinstance(item, Mapping) and item.get("requested_model") == model),
        None,
    ) if isinstance(results, list) else None
    if not isinstance(evidence, Mapping):
        fail("The selected Worker has no exact probe evidence.")
    if (
        evidence.get("status") != "FREE_ACTIVE"
        or evidence.get("response_model") != model
        or str(evidence.get("usage_cost")) not in {"0", "0.0", "0.00"}
        or evidence.get("fallback_used") is not False
        or evidence.get("credits_unchanged") is not True
        or evidence.get("provider_allow_fallbacks") is not False
    ):
        fail("The selected Worker did not satisfy the exact free endpoint contract.")
    return model


def call_agent(model: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    if not is_explicit_free_model(model):
        fail("Only an explicit :free endpoint may be called.")
    mission_id = os.environ.get("MISSION_ID", "MISSION-UNASSIGNED")
    agent_id = os.environ.get("AGENT_ID", "specialist-worker")
    request_id = hashlib.sha256(
        f"{mission_id}|{agent_id}|{model}|{system_prompt}|{user_prompt}".encode("utf-8")
    ).hexdigest()[:32]
    free_ledger = FreeUsageLedger()
    try:
        reservation = free_ledger.before_request(
            request_id=request_id,
            mission_id=mission_id,
            agent_id=agent_id,
            model=model,
        )
        if reservation.get("allowed") is not True:
            fail("Duplicate worker request was blocked by the OpenRouter ledger.")
        provider_registry = load_provider_registry()
        provider_ledger = ledger_from_registry(
            "openrouter",
            provider_registry,
            os.environ.get("PROVIDER_USAGE_LEDGER_PATH", "artifacts/provider_usage_ledger.json"),
        )
        adapter = GuardedProviderAdapter(
            OpenAICompatibleAdapter(provider_registry, "openrouter", network_enabled=True, timeout_seconds=TIMEOUT_SECONDS),
            provider_ledger,
        )
        response = adapter.generate(
            model,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=MAX_TOKENS,
            require_zero_cost=True,
            request_id=request_id,
            mission_id=mission_id,
            agent_id=agent_id,
        )
        free_ledger.record_response(request_id, success=True, http_status=200)
        return parse_output(response)
    except FreeQuotaBlocked as exc:
        fail(f"Free quota blocked: {exc.reason}")
    except QuotaGuardError as exc:
        free_ledger.record_response(request_id, success=False, http_status=None)
        fail(f"OpenRouter quota blocked: {exc.reason}")
    except ProviderAdapterError as exc:
        free_ledger.record_response(request_id, success=False, http_status=exc.http_status)
        if exc.error_class == "CREDIT_EXHAUSTED":
            fail("OpenRouter free credit is unavailable; no paid fallback was attempted.", 402)
        if exc.error_class in {"AUTH_ERROR", "PERMISSION_ERROR", "AUTH_NOT_CONFIGURED"}:
            fail("OpenRouter credentials or permission were rejected.", exc.http_status)
        if exc.error_class == "RATE_LIMITED":
            fail("OpenRouter free endpoint rate limit reached; no automatic retry was attempted.", 429)
        if exc.error_class == "NETWORK_TIMEOUT":
            fail("Specialist agent timed out.", 408)
        fail(f"OpenRouter worker request blocked: {exc.error_class}", exc.http_status)
    except KeyError:
        free_ledger.record_response(request_id, success=False, http_status=None)
        fail("OpenRouter worker configuration is incomplete.")
    except Exception:
        free_ledger.record_response(request_id, success=False, http_status=None)
        fail("Specialist agent request failed without exposing provider details.")


def instruction_flags(text: str) -> list[str]:
    checks = (
        (r"\b(?:git\s+push|git\s+merge|wrangler\s+deploy)\b", "repository_or_deploy_write"),
        (r"\b(?:create|rotate|replace|print|reveal)\s+(?:secret|token|password)\b", "secret_operation"),
        (r"\b(?:youtube\s+upload|publish\s+publicly|charge\s+the\s+user|payment)\b", "external_or_paid_operation"),
        (r"\b(?:paid\s+tavily|paid\s+search|buy\s+credits)\b", "paid_search_or_billing"),
    )
    lowered = text.lower()
    return [label for pattern, label in checks if re.search(pattern, lowered)]


def build_specialist_envelope(
    *,
    role: str,
    task_id: str,
    instruction: str,
    context: str,
    acceptance: str,
    model: str,
    artifact: dict[str, Any],
    result: dict[str, Any],
    next_tasks: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind one approved specialist result to the finite hierarchy."""
    registry = AgentRegistry()
    child_agent_id = specialist_for_role(role)
    child = registry.get(child_agent_id)
    parent_agent_id = bounded_identifier(
        os.environ.get("PARENT_AGENT_ID", ""),
        child.parent_agent_id or "google-general-commander",
    )
    mission_id = bounded_identifier(
        os.environ.get("MISSION_ID", ""),
        stable_id("MISSION", {"task_id": task_id, "instruction": instruction, "context": context}),
    )
    command_id = bounded_identifier(
        os.environ.get("COMMAND_ID", ""),
        stable_id("COMMAND", {"mission_id": mission_id, "task_id": task_id}),
    )
    parent_command_id = bounded_identifier(os.environ.get("PARENT_COMMAND_ID", ""), f"{mission_id}-P")
    context_payload = {"mission_id": mission_id, "context": context}
    context_ref = f"context-{stable_hash(context_payload)[:24]}"
    done_when = [clean(line, 400) for line in acceptance.splitlines() if clean(line, 400)]
    if not done_when:
        done_when = ["structured report returned", "commander approval remains required"]
    command = make_command(
        registry,
        mission_id=mission_id,
        command_id=command_id,
        parent_command_id=parent_command_id,
        parent_agent_id=parent_agent_id,
        child_agent_id=child_agent_id,
        mission=instruction,
        objective=instruction,
        constraints=(
            "read_only_draft",
            "commander_approval_required",
            "no_secret_or_binding_change",
            "no_publication_or_payment",
        ),
        input_refs=(context_ref,),
        expected_output={"schema": "report-envelope-v1", "artifact_type": artifact["type"]},
        token_budget=child.token_budget,
        time_budget_ms=child.time_budget_ms,
        tool_scope=child.allowed_tools,
        done_when=done_when,
        depth=2,
        inputs={"task_id": task_id, "context_ref": context_ref},
        parallel_group=None,
        priority=0,
    )
    artifact_ref = f"artifact-{stable_hash(artifact)[:24]}"
    report = ReportEnvelope(
        mission_id=mission_id,
        command_id=command.command_id,
        parent_command_id=command.parent_command_id,
        agent_id=command.child_agent_id,
        parent_agent_id=command.parent_agent_id,
        rank=command.rank,
        status="completed",
        summary=f"{role} specialist draft returned to commander",
        result={
            "artifact_type": artifact["type"],
            "artifact_ref": artifact_ref,
            "finding_count": len(result.get("findings", [])) if isinstance(result.get("findings"), list) else 0,
            "next_task_count": len(next_tasks),
        },
        artifacts=(artifact_ref,),
        evidence=("specialist-response-received",),
        warnings=("司令部の明示承認まで実行不可",),
        tokens_used=0,
        tools_used=command.tool_scope,
    )
    report.validate(command, registry)
    packet_context = project_context(
        instruction,
        command.constraints,
        {"role": role, "model": model, "acceptance": clean(acceptance, 1_200)},
        (context_ref,),
        "report-envelope-v1",
        budget=3_000,
    )
    envelope = {
        "mission_id": mission_id,
        "command": command.to_dict(),
        "report": report.to_dict(),
        "context_projection": packet_context,
        "hierarchy": {"parent_agent_id": command.parent_agent_id, "agent_id": command.child_agent_id, "rank": command.rank},
    }
    return envelope, report.to_dict()


def write_packet(packet: dict[str, Any]) -> str:
    encoded = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    path_value = os.environ.get("AGENT_PACKET_PATH", "").strip()
    if path_value:
        path = Path(path_value)
        if path.is_absolute() or ".." in path.parts:
            fail("Agent packet path must stay inside the workspace.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**packet, "packet_sha256": digest}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return digest


def main() -> int:
    if not (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("AI_API_KEY")):
        fail("OpenRouter API secret is not configured.")
    role = clean(os.environ.get("TASK_ROLE", "specialist"), 80) or "specialist"
    task_id = clean(os.environ.get("TASK_ID", "commander-task"), 100) or "commander-task"
    instruction = clean(os.environ.get("TASK_INSTRUCTION", ""), MAX_INSTRUCTION)
    context = clean(os.environ.get("TASK_CONTEXT", ""), MAX_CONTEXT)
    acceptance = clean(os.environ.get("TASK_ACCEPTANCE", ""), MAX_ACCEPTANCE)
    requested_model = os.environ.get("AI_MODEL", "").strip()
    parent_agent_id = bounded_identifier(os.environ.get("PARENT_AGENT_ID", ""), "google-general-commander")
    if not instruction:
        fail("Task instruction is required.")
    try:
        expected_parent = AgentRegistry().get(specialist_for_role(role)).parent_agent_id
    except Exception:
        fail("Specialist role is not present in the finite agent hierarchy.")
    if expected_parent != parent_agent_id:
        fail("Specialist role is not owned by the selected provider commander.")
    try:
        registry = load_registry(os.environ.get("MODEL_REGISTRY_PATH") or None)
    except Exception:
        fail("Model registry is invalid or unavailable.")
    parent_role = PARENT_ROLE_BY_AGENT.get(parent_agent_id)
    if parent_role is None:
        fail("PARENT_AGENT_ID must be an approved provider commander.")
    if role_config(registry, parent_role).get("active") is not True:
        fail("Parent commander role is inactive pending commander approval.")
    if role_config(registry, "ROLE_OPENROUTER_WORKER").get("active") is not True:
        fail("OpenRouter Worker role is inactive pending commander approval.")
    try:
        provider_registry = load_provider_registry()
        openrouter = provider_config(provider_registry, "openrouter")
    except Exception:
        fail("Provider registry is invalid or unavailable.")
    if (
        openrouter.get("enabled") is not True
        or openrouter.get("activation_approved") is not True
        or openrouter.get("probe_status") != "PROBE_OK"
        or openrouter.get("health_status") != "HEALTHY"
        or openrouter.get("circuit_state") != "CLOSED"
    ):
        fail("OpenRouter Worker provider is inactive pending probe and approval.")
    model = load_verified_worker_model(role, requested_model)

    system_prompt = (
        "あなたは司令部から一件だけ委任された専門エージェントです。"
        "指定された役割の調査・企画・文章・コード案・QA・指標分析を実行し、成果物ドラフトを作ってください。"
        "作業は読み取り・下書き生成に限定し、GitHub変更、ファイル書込み、デプロイ、公開、YouTube投稿、"
        "決済、有料検索、秘密値操作、外部サービスへの書込みは絶対に実行しないでください。"
        "追加の下位タスクを提案する場合も、必ず司令部承認が必要なread_only_draftとして記述してください。"
        "現在の人気や市場規模を断定せず、根拠・調査方法・不確実性を示してください。"
        "JSONのみ: {\"artifact\":{\"type\":\"...\",\"title\":\"...\",\"content\":\"...\"},"
        "\"findings\":[\"...\"],\"risks\":[\"...\"],\"tests\":[\"...\"],"
        "\"next_tasks\":[{\"id\":\"...\",\"role\":\"...\",\"instruction\":\"...\","
        "\"deliverable\":\"...\",\"acceptance_tests\":[\"...\"]}]}"
    )
    user_prompt = (
        "BEGIN_TASK\n"
        + instruction
        + "\nEND_TASK\nBEGIN_CONTEXT\n"
        + (context or "(なし)")
        + "\nEND_CONTEXT\nBEGIN_ACCEPTANCE\n"
        + (acceptance or "司令部が内容を確認でき、危険な操作を含まないこと")
        + "\nEND_ACCEPTANCE\n"
        + "成果物は司令部へ返し、あなた自身は次の実行を開始しないでください。"
    )
    result = call_agent(model, system_prompt, user_prompt)

    source_artifact = result.get("artifact") if isinstance(result.get("artifact"), dict) else {}
    artifact = {
        "type": clean(source_artifact.get("type") or f"{role}_draft", 80),
        "title": clean(source_artifact.get("title") or f"{role} agent draft", 200),
        "content": clean(source_artifact.get("content") or result.get("text"), 6000),
        "status": "draft_awaiting_commander_review",
    }
    next_tasks: list[dict[str, Any]] = []
    raw_tasks = result.get("next_tasks") if isinstance(result.get("next_tasks"), list) else []
    for index, item in enumerate(raw_tasks[:MAX_NEXT_TASKS], start=1):
        if not isinstance(item, dict):
            continue
        task_instruction = clean(item.get("instruction"), 900)
        if not task_instruction:
            continue
        next_tasks.append(
            {
                "id": clean(item.get("id") or f"{task_id}-next-{index:02d}", 100),
                "role": clean(item.get("role") or "specialist", 80),
                "instruction": task_instruction,
                "deliverable": clean(item.get("deliverable") or "次の提案ドラフト", 500),
                "acceptance_tests": clean_list(item.get("acceptance_tests"), item_limit=400),
                "execution_mode": "read_only_draft",
                "requires_commander_approval": True,
                "execution_allowed": False,
                "safety_flags": instruction_flags(task_instruction),
            }
        )

    try:
        hierarchy_envelope, report_envelope = build_specialist_envelope(
            role=role,
            task_id=task_id,
            instruction=instruction,
            context=context,
            acceptance=acceptance,
            model=model,
            artifact=artifact,
            result=result,
            next_tasks=next_tasks,
        )
    except Exception:
        fail("Specialist result could not be bound to the commander hierarchy.")

    packet: dict[str, Any] = {
        "ok": True,
        "status": "awaiting_commander_approval",
        "mode": "commander_approved_specialist_draft_with_envelope",
        "mission_id": hierarchy_envelope["mission_id"],
        "command": hierarchy_envelope["command"],
        "report": report_envelope,
        "context_projection": hierarchy_envelope["context_projection"],
        "hierarchy": hierarchy_envelope["hierarchy"],
        "task": {"id": task_id, "role": role, "instruction": instruction, "acceptance": acceptance},
        "artifact": artifact,
        "findings": clean_list(result.get("findings")),
        "risks": clean_list(result.get("risks")),
        "tests": clean_list(result.get("tests")),
        "next_tasks": next_tasks,
        "authority": {
            "commander": "final_decision_and_execution",
            "agent_scope": "draft_only",
            "execution_allowed": False,
            "required_confirmation": "COMMANDER_APPROVE",
            "prohibited": [
                "repository_write",
                "deploy_or_publish",
                "secret_or_binding_change",
                "payment_or_paid_search",
                "YouTube_upload",
            ],
        },
        "budget": {
            "provider": "openrouter",
            "calls": 1,
            "max_tokens": MAX_TOKENS,
            "timeout_seconds": TIMEOUT_SECONDS,
            "automatic_fallback": False,
            "paid_search": "disabled",
        },
    }
    digest = write_packet(packet)
    print(
        json.dumps(
            {
                "ok": True,
                "status": packet["status"],
                "task_id": task_id,
                "role": role,
                "artifact_type": artifact["type"],
                "next_task_count": len(next_tasks),
                "packet_sha256": digest,
                "execution_allowed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
