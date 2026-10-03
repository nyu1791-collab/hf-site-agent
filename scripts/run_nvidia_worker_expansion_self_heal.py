#!/usr/bin/env python3
"""Run the compact NVIDIA commander with bounded same-mission self-healing.

The first commander result is validated normally. If it is incomplete solely in
a repairable structured-result class (currently INVALID_PATHS or
PARTIAL_TRUNCATED), and the first provider reservation is known SETTLED, this
wrapper persists the current generation into the existing durable resume area
and permits exactly one continuation of the SAME mission.

The continuation is deliberately different from the first synthesis call: it
receives only the valid paths already mentioned by the partial result when such
paths exist, a tiny prior-result diagnostic, and an explicit compact JSON
schema. It therefore repairs the transport/structure failure instead of paying
for another broad repository analysis. No repository write, production
activation, paid fallback, secret mutation, or unbounded retry is introduced.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover - direct workflow entrypoint
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import run_nvidia_orchestrator_guarded as guarded
from scripts import run_nvidia_orchestrator_mission as mission
from scripts import run_nvidia_worker_expansion_compact as compact


REPORT_PATH = Path("artifacts/commander_self_heal.json")
REPAIRABLE_STATUSES = frozenset({"INVALID_PATHS", "PARTIAL_TRUNCATED"})
MAX_SELF_HEAL_CONTINUATIONS = 1
SCOPE_CONTRACT_VERSION = "exact-path-contract-v2"
MAX_REPAIR_PATHS = 2
MAX_REPAIR_EVIDENCE_CHARS = 2_400

REPAIR_OBJECTIVE = (
    " REPAIR-ONLY CONTINUATION. The prior NVIDIA generation already performed the engineering analysis. "
    "Do not rescan the organization, do not restate repository context, and do not propose a different project. "
    "Repair only the incomplete Structured Patch Bundle using the supplied previous_partial evidence. "
    "Choose at most 2 allowed paths and at most 2 operations. Keep the complete JSON under 900 visible tokens. "
    "Each exact_change and operation must be one concise deterministic edit; tests and safety_invariants must each "
    "contain at least one short item. Return JSON only."
)


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(dict(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def exact_allowed_paths() -> list[str]:
    """Return only focused paths that are present in the checked-out source."""
    return sorted({path for path in compact.FOCUSED_FILES if Path(path).is_file()})


def _compact_json_template(paths: list[str]) -> str:
    example_path = paths[0] if paths else "<copy-one-allowed-path-verbatim>"
    template = {
        "files_to_change": [example_path],
        "exact_changes": [
            {
                "path": example_path,
                "symbol": "<real-symbol-from-context>",
                "exact_change": "<one concise deterministic change>",
                "rationale": "<brief evidence-grounded reason>",
            }
        ],
        "patch_bundle": {
            "operations": [
                {
                    "path": example_path,
                    "symbol": "<same-real-symbol>",
                    "change": "<same concise deterministic change>",
                    "rationale": "<brief reason>",
                }
            ]
        },
        "tests": ["<one bounded deterministic test>"],
        "safety_invariants": ["no secrets, no production activation, no generic paid fallback"],
        "next_action": "WORK_REVIEW",
    }
    return json.dumps(template, ensure_ascii=False, separators=(",", ":"))


def scope_contract(paths: list[str]) -> str:
    encoded = json.dumps(paths, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
    diagnostic = str(os.environ.get("NVIDIA_REPAIR_DIAGNOSTIC") or "")[:1200]
    repair = (
        " The previous generation was machine-rejected for this bounded reason: "
        + diagnostic
        + ". Correct only that contract failure; do not restart broad analysis. "
        + "Return a complete object shaped like this compact template, replacing every placeholder with grounded values: "
        + _compact_json_template(paths)
        if diagnostic
        else ""
    )
    return (
        f" SCOPE_CONTRACT_VERSION={SCOPE_CONTRACT_VERSION}; SCOPE_DIGEST={digest}. "
        f"ALLOWED_PATHS_EXACT={encoded}. "
        "Every files_to_change item and every patch_bundle.operations[].path MUST be copied verbatim from "
        "ALLOWED_PATHS_EXACT. Do not output a path merely because it exists elsewhere in the repository. "
        "Do not infer renamed, adjacent, historical, or unsupplied files. files_to_change MUST equal the unique "
        "operation paths. If the desired change needs a path outside this list, use only listed files for a valid "
        "alternative or set next_action=REQUEST_CONTEXT; never invent the missing path. Before returning JSON, "
        "internally verify exact membership for every emitted path."
        + repair
    )


def invalid_paths(inbox: Mapping[str, Any]) -> list[str]:
    allowed = {str(item) for item in inbox.get("repository_context_files", []) if isinstance(item, str)}
    proposal = inbox.get("proposal") if isinstance(inbox.get("proposal"), Mapping) else {}
    found: list[str] = []
    files = proposal.get("files_to_change") if isinstance(proposal.get("files_to_change"), list) else []
    for item in files:
        if isinstance(item, str) and item not in allowed and item not in found:
            found.append(item)
    bundle = proposal.get("patch_bundle") if isinstance(proposal.get("patch_bundle"), Mapping) else {}
    operations = bundle.get("operations") if isinstance(bundle.get("operations"), list) else []
    for operation in operations:
        if not isinstance(operation, Mapping):
            continue
        path = operation.get("path")
        if isinstance(path, str) and path not in allowed and path not in found:
            found.append(path)
    return found


def _proposal_text(inbox: Mapping[str, Any]) -> str:
    proposal = inbox.get("proposal") if isinstance(inbox.get("proposal"), Mapping) else {}
    chunks: list[str] = []
    for key in ("proposal", "summary"):
        value = proposal.get(key)
        if isinstance(value, str) and value:
            chunks.append(value)
    if proposal:
        try:
            chunks.append(json.dumps(proposal, ensure_ascii=False, sort_keys=True))
        except Exception:
            pass
    return "\n".join(chunks)[:12_000]


def repair_paths(inbox: Mapping[str, Any], allowed_paths: list[str]) -> list[str]:
    """Reuse only valid paths the first generation already selected.

    This avoids turning a structural continuation into a second design mission.
    If the partial output did not mention any valid path, retain the original
    focused set rather than guessing a new file.
    """
    text = _proposal_text(inbox)
    mentioned = [path for path in allowed_paths if path in text]
    return mentioned[:MAX_REPAIR_PATHS]


def repair_evidence(inbox: Mapping[str, Any], paths: list[str]) -> str:
    payload = {
        "previous_status": inbox.get("result_status", inbox.get("status")),
        "previous_result_hash": inbox.get("result_hash"),
        "valid_paths_already_selected": paths,
        "previous_partial": _proposal_text(inbox)[:MAX_REPAIR_EVIDENCE_CHARS],
        "instruction": "preserve the prior engineering intent and complete only the required structured envelope",
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def should_self_heal(inbox: Mapping[str, Any]) -> tuple[bool, str]:
    status = str(inbox.get("result_status") or inbox.get("status") or "")
    if status not in REPAIRABLE_STATUSES:
        return False, "RESULT_NOT_REPAIRABLE"
    if inbox.get("result_hash_verified") is not True:
        return False, "RESULT_HASH_NOT_VERIFIED"
    if str(inbox.get("provider_reservation_state") or "") != "SETTLED":
        return False, "FIRST_CALL_NOT_SETTLED"
    if not str(inbox.get("mission_id") or "") or not str(inbox.get("run_id") or "") or not str(inbox.get("result_hash") or ""):
        return False, "RESUME_IDENTITY_INCOMPLETE"
    return True, "BOUNDED_SELF_HEAL_ALLOWED"


def _prepare_resume(inbox: Mapping[str, Any]) -> None:
    resume_root = guarded.RESUME_ROOT
    resume_root.mkdir(parents=True, exist_ok=True)
    sources = {
        Path(mission.MISSION_STATE): resume_root / "mission_state.json",
        Path(mission.RESULT_INBOX): resume_root / "result_inbox.json",
        guarded.LEDGER_PATH: resume_root / guarded.LEDGER_PATH.name,
    }
    for source, target in sources.items():
        if not source.is_file():
            raise RuntimeError(f"resume source unavailable: {source}")
        shutil.copyfile(source, target)

    os.environ["RESUME_MISSION_ID"] = str(inbox["mission_id"])
    os.environ["RESUME_RUN_ID"] = str(inbox["run_id"])
    os.environ["RESUME_RESULT_HASH"] = str(inbox["result_hash"])
    os.environ["RESUME_REVISION"] = str(int(inbox.get("revision", 0) or 0) + 1)


def _annotate_final_inbox(
    *,
    attempts: int,
    first_status: str,
    first_invalid_paths: list[str],
    repair_paths_used: list[str],
) -> None:
    path = Path(mission.RESULT_INBOX)
    inbox = _read(path)
    if not inbox:
        return
    inbox["commander_self_heal"] = {
        "enabled": True,
        "continuation_count": attempts,
        "max_continuations": MAX_SELF_HEAL_CONTINUATIONS,
        "first_result_status": first_status,
        "first_invalid_paths": first_invalid_paths,
        "same_mission_resume": attempts > 0,
        "scope_contract_version": SCOPE_CONTRACT_VERSION,
        "repair_only_context": attempts > 0,
        "repair_paths_used": repair_paths_used,
        "unbounded_retry": False,
    }
    inbox["nvidia_external_calls_mission_total"] = 1 + attempts
    _write(path, inbox)


def main() -> int:
    original_prompt = mission._mission_prompt
    original_compact_files = compact.FOCUSED_FILES
    original_compact_objective = compact.COMPACT_OBJECTIVE
    original_compact_context = compact.compact_council_context
    saved_env = {
        key: os.environ.get(key)
        for key in (
            "RESUME_MISSION_ID",
            "RESUME_RUN_ID",
            "RESUME_RESULT_HASH",
            "RESUME_REVISION",
            "NVIDIA_REPAIR_DIAGNOSTIC",
        )
    }
    allowed_paths = exact_allowed_paths()

    def strict_prompt(*, resume: bool, previous_hash: str, mission_mode: str) -> str:
        return original_prompt(resume=resume, previous_hash=previous_hash, mission_mode=mission_mode) + scope_contract(
            list(compact.FOCUSED_FILES) if resume else allowed_paths
        )

    mission._mission_prompt = strict_prompt
    continuation_count = 0
    selected_repair_paths: list[str] = []
    try:
        first_rc = compact.main()
        first = _read(Path(mission.RESULT_INBOX))
        first_status = str(first.get("result_status") or first.get("status") or "UNKNOWN")
        first_invalid = invalid_paths(first)
        allowed, reason = should_self_heal(first)
        report: dict[str, Any] = {
            "schema_version": "commander-self-heal-v2",
            "scope_contract_version": SCOPE_CONTRACT_VERSION,
            "allowed_path_count": len(allowed_paths),
            "first_return_code": first_rc,
            "first_result_status": first_status,
            "first_invalid_paths": first_invalid,
            "self_heal_allowed": allowed,
            "self_heal_decision": reason,
            "continuation_count": 0,
            "max_continuations": MAX_SELF_HEAL_CONTINUATIONS,
            "same_mission_resume": False,
            "repair_only_context": False,
            "repair_paths_used": [],
            "unbounded_retry": False,
            "repository_write": False,
            "paid_fallback": False,
            "production_active": False,
        }
        if first_rc != 0 or not allowed:
            report["final_result_status"] = first_status
            _write(REPORT_PATH, report)
            _annotate_final_inbox(
                attempts=0,
                first_status=first_status,
                first_invalid_paths=first_invalid,
                repair_paths_used=[],
            )
            return first_rc

        _prepare_resume(first)
        selected_repair_paths = repair_paths(first, allowed_paths)
        active_repair_paths = selected_repair_paths or allowed_paths
        diagnostic_payload = {
            "status": first_status,
            "invalid_paths": first_invalid,
            "repair_paths": active_repair_paths[:MAX_REPAIR_PATHS],
            "instruction": "continue same mission and repair structured contract only",
        }
        os.environ["NVIDIA_REPAIR_DIAGNOSTIC"] = json.dumps(
            diagnostic_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )[:1200]

        # Keep the second provider call focused on the first generation's own
        # valid file choices. This materially shrinks the prompt and prevents a
        # structural retry from becoming another broad engineering mission.
        if selected_repair_paths:
            compact.FOCUSED_FILES = tuple(selected_repair_paths)
        compact.COMPACT_OBJECTIVE = REPAIR_OBJECTIVE
        compact.compact_council_context = lambda: repair_evidence(first, active_repair_paths[:MAX_REPAIR_PATHS])

        continuation_count = 1
        second_rc = compact.main()
        final = _read(Path(mission.RESULT_INBOX))
        report.update({
            "continuation_count": continuation_count,
            "same_mission_resume": True,
            "repair_only_context": True,
            "repair_paths_used": active_repair_paths[:MAX_REPAIR_PATHS],
            "second_return_code": second_rc,
            "final_result_status": final.get("result_status", final.get("status")),
            "final_result_complete": final.get("result_complete") is True,
            "final_path_validation": final.get("path_validation") is True,
            "final_revision": final.get("revision"),
            "final_result_hash_verified": final.get("result_hash_verified") is True,
        })
        _write(REPORT_PATH, report)
        _annotate_final_inbox(
            attempts=continuation_count,
            first_status=first_status,
            first_invalid_paths=first_invalid,
            repair_paths_used=active_repair_paths[:MAX_REPAIR_PATHS],
        )
        return second_rc
    finally:
        mission._mission_prompt = original_prompt
        compact.FOCUSED_FILES = original_compact_files
        compact.COMPACT_OBJECTIVE = original_compact_objective
        compact.compact_council_context = original_compact_context
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    raise SystemExit(main())
