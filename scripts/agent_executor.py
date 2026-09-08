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
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

try:
    from scripts.agent_runtime import AgentRegistry, ReportEnvelope, make_command, project_context, stable_hash, stable_id
except ModuleNotFoundError:  # pragma: no cover - when invoked from scripts/
    from agent_runtime import AgentRegistry, ReportEnvelope, make_command, project_context, stable_hash, stable_id


ROLE_TO_SPECIALIST = {
    "research": "research-specialist",
    "product": "product-specialist",
    "content": "content-specialist",
    "video": "video-specialist",
    "code": "code-specialist",
    "qa": "qa-specialist",
    "metrics": "metrics-specialist",
    "specialist": "product-specialist",
    "specialist_commander": "product-specialist",
}

MAX_INSTRUCTION = 5000
MAX_CONTEXT = 6000
MAX_ACCEPTANCE = 3000
MAX_OUTPUT_CHARS = 7000
MAX_MODEL_CHARS = 160
MAX_TOKENS = 500
TIMEOUT_SECONDS = 15.0
MAX_NEXT_TASKS = 4
BASE_URL = "https://openrouter.ai/api/v1"
MODEL_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,160}$")
FREE_MODEL_RE = re.compile(r"^(?:openrouter/free|[A-Za-z0-9._/-]+:free)$")
SECRET_PATTERNS = (
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)(?:sk|gsk|hf|sk-or-v1)-[A-Za-z0-9_-]{12,}"),
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


def call_agent(model: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    try:
        client = OpenAI(
            api_key=os.environ["AI_API_KEY"],
            base_url=BASE_URL,
            timeout=TIMEOUT_SECONDS,
            max_retries=0,
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=MAX_TOKENS,
            stream=False,
        )
        return parse_output(response)
    except KeyError:
        fail("AI_API_KEY secret is not configured.")
    except APITimeoutError:
        fail("Specialist agent timed out.", 408)
    except APIConnectionError:
        fail("Specialist agent connection failed.")
    except APIStatusError as exc:
        status = getattr(exc, "status_code", None)
        if status == 402:
            fail("OpenRouter free credit is unavailable; no paid fallback was attempted.", 402)
        if status in (401, 403):
            fail("OpenRouter credentials or permission were rejected.", status)
        if status == 429:
            fail("OpenRouter rate limit reached; no automatic fallback was attempted.", 429)
        fail("OpenRouter returned a non-success response.", status if isinstance(status, int) else None)
    except Exception:
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
    parent_agent_id = bounded_identifier(os.environ.get("PARENT_AGENT_ID", ""), child.parent_agent_id or "qwen-planner")
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
    if not os.environ.get("AI_API_KEY"):
        fail("AI_API_KEY secret is not configured.")
    role = clean(os.environ.get("TASK_ROLE", "specialist"), 80) or "specialist"
    task_id = clean(os.environ.get("TASK_ID", "commander-task"), 100) or "commander-task"
    instruction = clean(os.environ.get("TASK_INSTRUCTION", ""), MAX_INSTRUCTION)
    context = clean(os.environ.get("TASK_CONTEXT", ""), MAX_CONTEXT)
    acceptance = clean(os.environ.get("TASK_ACCEPTANCE", ""), MAX_ACCEPTANCE)
    model = os.environ.get("AI_MODEL", "qwen/qwen3-32b:free").strip()
    if not instruction:
        fail("Task instruction is required.")
    if not MODEL_RE.fullmatch(model) or not FREE_MODEL_RE.fullmatch(model):
        fail("AI model must be an OpenRouter free model ID.")
    if len(model) > MAX_MODEL_CHARS:
        fail("AI model is too long.")

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

