#!/usr/bin/env python3
"""Run the compact NVIDIA commander with bounded same-mission self-healing.

The first commander result is validated normally. If it is incomplete solely in
a repairable structured-result class (currently INVALID_PATHS or
PARTIAL_TRUNCATED), and the first provider reservation is known SETTLED, this
wrapper persists the current generation into the existing durable resume area
and permits exactly one continuation of the SAME mission. The continuation gets
an explicit exact-path contract and a compact failure diagnostic, avoiding a
new mission and avoiding broad repeated analysis.

No repository write, production activation, paid fallback, secret mutation, or
unbounded retry is introduced here.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import run_nvidia_orchestrator_guarded as guarded
from scripts import run_nvidia_orchestrator_mission as mission
from scripts import run_nvidia_worker_expansion_compact as compact


REPORT_PATH = Path("artifacts/commander_self_heal.json")
REPAIRABLE_STATUSES = frozenset({"INVALID_PATHS", "PARTIAL_TRUNCATED"})
MAX_SELF_HEAL_CONTINUATIONS = 1
SCOPE_CONTRACT_VERSION = "exact-path-contract-v1"


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


def scope_contract(paths: list[str]) -> str:
    encoded = json.dumps(paths, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
    diagnostic = str(os.environ.get("NVIDIA_REPAIR_DIAGNOSTIC") or "")[:1200]
    repair = (
        " The previous generation was machine-rejected for this bounded reason: "
        + diagnostic
        + ". Correct only that contract failure; do not restart broad analysis."
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


def _annotate_final_inbox(*, attempts: int, first_status: str, first_invalid_paths: list[str]) -> None:
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
        "unbounded_retry": False,
    }
    inbox["nvidia_external_calls_mission_total"] = 1 + attempts
    _write(path, inbox)


def main() -> int:
    original_prompt = mission._mission_prompt
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
        return original_prompt(resume=resume, previous_hash=previous_hash, mission_mode=mission_mode) + scope_contract(allowed_paths)

    mission._mission_prompt = strict_prompt
    continuation_count = 0
    try:
        first_rc = compact.main()
        first = _read(Path(mission.RESULT_INBOX))
        first_status = str(first.get("result_status") or first.get("status") or "UNKNOWN")
        first_invalid = invalid_paths(first)
        allowed, reason = should_self_heal(first)
        report: dict[str, Any] = {
            "schema_version": "commander-self-heal-v1",
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
            "unbounded_retry": False,
            "repository_write": False,
            "paid_fallback": False,
            "production_active": False,
        }
        if first_rc != 0 or not allowed:
            report["final_result_status"] = first_status
            _write(REPORT_PATH, report)
            _annotate_final_inbox(attempts=0, first_status=first_status, first_invalid_paths=first_invalid)
            return first_rc

        _prepare_resume(first)
        diagnostic_payload = {
            "status": first_status,
            "invalid_paths": first_invalid,
            "allowed_paths": allowed_paths,
            "instruction": "continue same mission and repair structured contract only",
        }
        os.environ["NVIDIA_REPAIR_DIAGNOSTIC"] = json.dumps(
            diagnostic_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )[:1200]
        continuation_count = 1
        second_rc = compact.main()
        final = _read(Path(mission.RESULT_INBOX))
        report.update({
            "continuation_count": continuation_count,
            "same_mission_resume": True,
            "second_return_code": second_rc,
            "final_result_status": final.get("result_status", final.get("status")),
            "final_result_complete": final.get("result_complete") is True,
            "final_path_validation": final.get("path_validation") is True,
            "final_revision": final.get("revision"),
            "final_result_hash_verified": final.get("result_hash_verified") is True,
        })
        _write(REPORT_PATH, report)
        _annotate_final_inbox(attempts=continuation_count, first_status=first_status, first_invalid_paths=first_invalid)
        return second_rc
    finally:
        mission._mission_prompt = original_prompt
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    raise SystemExit(main())
