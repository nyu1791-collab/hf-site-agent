#!/usr/bin/env python3
"""Run the existing NVIDIA Lead Engineer mission behind durable call guards.

The underlying mission prompt/parser remains unchanged.  This wrapper adds:
* Resume identity validation against persisted Mission State + Result Inbox.
* Cross-run reservation-ledger restoration.
* A provider-call reservation before NVIDIA dispatch.
* Conservative UNSETTLED accounting after uncertain failures.
* Source-HEAD and Result-Hash binding on persisted delivery artifacts.

It never enables production, paid fallback, repository writes, deploy, publish,
or secret persistence.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from typing import Any, Mapping

from scripts import run_nvidia_orchestrator_mission as mission
from scripts.mission_integrity import HEAD_RE, result_hash, validate_resume_bundle
from scripts.mission_scheduler import MissionReservationLedger
from scripts.provider_adapters import ProviderAdapterError


LEDGER_PATH = Path("artifacts/nvidia_orchestrator_ledger.json")
RESUME_ROOT = Path("artifacts/resume")
MAX_MISSION_REQUESTS = 8
CALL_TOKEN_RESERVATION = 32_768
MAX_MISSION_TOKEN_BUDGET = MAX_MISSION_REQUESTS * CALL_TOKEN_RESERVATION


def _read(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, Mapping) else {}


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(dict(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _parse_revision(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = int(text)
    except ValueError:
        return -1
    return parsed


def _blocked(reason: str, *, source_head: str) -> int:
    state = {
        "schema_version": "mission-state-v3",
        "mission_id": os.environ.get("RESUME_MISSION_ID", "").strip() or "UNSTARTED",
        "run_id": os.environ.get("RESUME_RUN_ID", "").strip() or os.environ.get("GITHUB_RUN_ID", "local"),
        "revision": _parse_revision(os.environ.get("RESUME_REVISION", "")) or 0,
        "state": "BLOCKED",
        "result_status": "BLOCKED",
        "delivery_state": "DELIVERY_PENDING",
        "source_head": source_head,
        "stop_reason": reason,
        "nvidia_external_calls": 0,
        "nvidia_external_calls_uncertain": 0,
        "paid_execution_count": 0,
        "paid_fallback_count": 0,
        "production_active": False,
        "secret_values_persisted": 0,
    }
    _write(Path(mission.MISSION_STATE), state)
    _write(Path(mission.RESULT_INBOX), {
        "schema_version": "result-inbox-v3",
        "mission_id": state["mission_id"],
        "run_id": state["run_id"],
        "revision": state["revision"],
        "status": "BLOCKED",
        "result_status": "BLOCKED",
        "delivery_state": "DELIVERY_PENDING",
        "source_head": source_head,
        "stop_reason": reason,
        "nvidia_external_calls": 0,
        "paid_execution_count": 0,
        "paid_fallback_count": 0,
        "production_active": False,
        "secret_values_persisted": 0,
    })
    print(json.dumps({"state": "BLOCKED", "stop_reason": reason, "source_head": source_head}, sort_keys=True))
    return 0


class DurableNvidiaAdapter:
    """Delegate to the real adapter while persisting one dispatch reservation."""

    def __init__(self, underlying: Any, ledger: MissionReservationLedger, source_head: str):
        self.underlying = underlying
        self.ledger = ledger
        self.source_head = source_head
        self.provider_id = getattr(underlying, "provider_id", "nvidia")
        self.config = getattr(underlying, "config", {})
        self.current_reservation_id = ""
        self.current_dispatch_started = False
        self.current_state = "NOT_CALLED"
        self.calls_this_carrier = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.underlying, name)

    @staticmethod
    def _usage_tokens(result: Mapping[str, Any]) -> int:
        usage = result.get("usage") if isinstance(result.get("usage"), Mapping) else {}
        values = (
            usage.get("prompt_tokens", usage.get("promptTokenCount", 0)),
            usage.get("completion_tokens", usage.get("candidatesTokenCount", 0)),
        )
        total = 0
        for value in values:
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                total += value
        return total

    def generate(self, model_id: str, messages: Any, **options: Any) -> dict[str, Any]:
        if self.calls_this_carrier >= 1:
            raise ProviderAdapterError("CARRIER_NVIDIA_CALL_BUDGET_EXHAUSTED")

        request_id = str(options.get("request_id") or "").strip()
        mission_id = str(options.get("mission_id") or "").strip()
        if not request_id or not mission_id:
            raise ProviderAdapterError("REQUEST_IDENTITY_REQUIRED")

        self.ledger.register_mission(
            mission_id,
            request_budget=MAX_MISSION_REQUESTS,
            token_budget=MAX_MISSION_TOKEN_BUDGET,
            provider_request_budgets={"nvidia": MAX_MISSION_REQUESTS},
            provider_token_budgets={"nvidia": MAX_MISSION_TOKEN_BUDGET},
        )
        reservation = self.ledger.reserve(
            mission_id=mission_id,
            task_id="lead-engineer-build",
            provider_id="nvidia",
            idempotency_key=request_id,
            payload={
                "model": model_id,
                "request_id": request_id,
                "source_head": self.source_head,
                "operation": "NVIDIA_LEAD_ENGINEER",
            },
            requested_requests=1,
            requested_tokens=CALL_TOKEN_RESERVATION,
        )
        self.current_reservation_id = str(reservation.get("reservation_id") or "")
        if reservation.get("dispatch_started") is True or reservation.get("state") in {"settled", "unsettled"}:
            self.current_state = str(reservation.get("state") or "UNKNOWN").upper()
            raise ProviderAdapterError("DUPLICATE_NVIDIA_CALL_BLOCKED")

        self.ledger.mark_dispatched(self.current_reservation_id)
        self.current_dispatch_started = True
        self.current_state = "DISPATCH_STARTED"
        self.calls_this_carrier += 1
        try:
            result = self.underlying.generate(model_id, messages, **options)
        except Exception:
            self.ledger.mark_unsettled(self.current_reservation_id, reason="provider_dispatch_completed_but_result_unknown")
            self.current_state = "UNSETTLED"
            raise

        if not isinstance(result, Mapping):
            self.ledger.mark_unsettled(self.current_reservation_id, reason="provider_response_not_mapping")
            self.current_state = "UNSETTLED"
            raise ProviderAdapterError("INVALID_PROVIDER_RESPONSE")

        tokens = self._usage_tokens(result)
        actual_tokens = tokens if tokens > 0 else CALL_TOKEN_RESERVATION
        self.ledger.settle(
            self.current_reservation_id,
            actual_requests=1,
            actual_tokens=actual_tokens,
            reason="provider_result_persistable",
        )
        self.current_state = "SETTLED"
        return dict(result)


def _enrich_artifacts(source_head: str, adapter: DurableNvidiaAdapter | None, resume_report: Mapping[str, Any]) -> None:
    state_path = Path(mission.MISSION_STATE)
    inbox_path = Path(mission.RESULT_INBOX)
    state = dict(_read(state_path))
    inbox = dict(_read(inbox_path))

    if state:
        state["schema_version"] = "mission-state-v3"
        state["source_head"] = source_head
        if resume_report.get("resume") is True:
            state["previous_source_head"] = resume_report.get("previous_source_head")
            state["head_advanced_on_resume"] = resume_report.get("head_advanced") is True
        if adapter is not None:
            state["provider_reservation_state"] = adapter.current_state
            if adapter.current_dispatch_started:
                state["nvidia_external_calls"] = max(1, int(state.get("nvidia_external_calls", 0) or 0))
                state["nvidia_external_calls_uncertain"] = 1 if adapter.current_state == "UNSETTLED" else 0
        state["reservation_ledger"] = str(LEDGER_PATH)
        _write(state_path, state)

    if inbox:
        inbox["schema_version"] = "result-inbox-v3"
        inbox["source_head"] = source_head
        inbox.setdefault("delivery_state", "DELIVERY_PENDING")
        if resume_report.get("resume") is True:
            inbox["previous_source_head"] = resume_report.get("previous_source_head")
            inbox["head_advanced_on_resume"] = resume_report.get("head_advanced") is True
        proposal = inbox.get("proposal")
        digest = str(inbox.get("result_hash") or "")
        inbox["result_hash_verified"] = isinstance(proposal, Mapping) and bool(digest) and result_hash(proposal) == digest
        if adapter is not None:
            inbox["provider_reservation_state"] = adapter.current_state
            if adapter.current_dispatch_started:
                inbox["nvidia_external_calls"] = max(1, int(inbox.get("nvidia_external_calls", 0) or 0))
                inbox["nvidia_external_calls_uncertain"] = 1 if adapter.current_state == "UNSETTLED" else 0
        _write(inbox_path, inbox)


def main() -> int:
    source_head = str(os.environ.get("SOURCE_HEAD") or os.environ.get("GITHUB_SHA") or "").strip()
    if not HEAD_RE.fullmatch(source_head):
        return _blocked("CURRENT_SOURCE_HEAD_INVALID", source_head=source_head or "0" * 40)

    resume_mission_id = os.environ.get("RESUME_MISSION_ID", "").strip()
    resume_run_id = os.environ.get("RESUME_RUN_ID", "").strip()
    resume_hash = os.environ.get("RESUME_RESULT_HASH", "").strip()
    requested_revision = _parse_revision(os.environ.get("RESUME_REVISION", ""))

    previous_state = _read(RESUME_ROOT / "mission_state.json")
    previous_inbox = _read(RESUME_ROOT / "result_inbox.json")
    resume_report = validate_resume_bundle(
        mission_id=resume_mission_id,
        run_id=resume_run_id,
        previous_result_hash=resume_hash,
        requested_revision=requested_revision,
        source_head=source_head,
        mission_state=previous_state,
        result_inbox=previous_inbox,
    )
    if not resume_report.get("valid"):
        return _blocked(str(resume_report.get("reason") or "RESUME_INTEGRITY_FAILED"), source_head=source_head)

    if resume_report.get("resume") is True:
        previous_ledger = RESUME_ROOT / LEDGER_PATH.name
        if not previous_ledger.is_file():
            return _blocked("RESUME_LEDGER_UNAVAILABLE", source_head=source_head)
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(previous_ledger, LEDGER_PATH)
        os.environ["RESUME_REVISION"] = str(resume_report["next_revision"])

    os.environ["GITHUB_SHA"] = source_head

    extra_context = (
        "scripts/mission_integrity.py",
        "scripts/run_nvidia_orchestrator_guarded.py",
        "scripts/google_staging_readiness.py",
        "tests/test_mission_integrity.py",
        "tests/test_google_staging_readiness.py",
    )
    mission.ALLOWED_FILES = tuple(dict.fromkeys((*extra_context, *mission.ALLOWED_FILES)))
    mission.CONTEXT_MARKERS = {
        **dict(mission.CONTEXT_MARKERS),
        "scripts/mission_integrity.py": (
            "def validate_result_inbox",
            "def validate_resume_bundle",
            "def acknowledge_result_inbox",
        ),
        "scripts/run_nvidia_orchestrator_guarded.py": (
            "class DurableNvidiaAdapter",
            "def _enrich_artifacts",
            "def main",
        ),
        "scripts/google_staging_readiness.py": (
            "def build_google_readiness_packet",
            "validate_result_inbox",
            "repeat_nvidia_call_allowed",
        ),
    }

    ledger = MissionReservationLedger(
        LEDGER_PATH,
        provider_limits={"nvidia": {"requests": MAX_MISSION_REQUESTS, "tokens": MAX_MISSION_TOKEN_BUDGET}},
    )
    original_factory = mission.create_provider_adapter
    wrappers: list[DurableNvidiaAdapter] = []

    def guarded_factory(registry: Mapping[str, Any], provider_id: str, **kwargs: Any) -> Any:
        underlying = original_factory(registry, provider_id, **kwargs)
        if provider_id != "nvidia":
            return underlying
        wrapper = DurableNvidiaAdapter(underlying, ledger, source_head)
        wrappers.append(wrapper)
        return wrapper

    mission.create_provider_adapter = guarded_factory
    try:
        rc = mission.main()
    finally:
        mission.create_provider_adapter = original_factory

    adapter = wrappers[-1] if wrappers else None
    _enrich_artifacts(source_head, adapter, resume_report)
    state = _read(Path(mission.MISSION_STATE))
    inbox = _read(Path(mission.RESULT_INBOX))
    print(json.dumps({
        "guarded_nvidia_mission": True,
        "mission_id": state.get("mission_id"),
        "run_id": state.get("run_id"),
        "revision": state.get("revision"),
        "source_head": source_head,
        "provider_reservation_state": adapter.current_state if adapter else "NOT_CALLED",
        "nvidia_external_calls": state.get("nvidia_external_calls", 0),
        "result_status": state.get("result_status"),
        "result_hash": inbox.get("result_hash"),
        "result_hash_verified": inbox.get("result_hash_verified") is True,
        "production_active": False,
        "paid_fallback": False,
    }, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
