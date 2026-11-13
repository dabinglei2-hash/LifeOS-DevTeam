"""Explicit controller-only exception; never selected by the team CLI or retry.

The caller must supply a task/candidate-bound user grant and actual broker proof.
This does not impersonate an independent QA agent or reviewer.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from lads_knowledge.safety import canonical_json
from .engine import AutomationEngine, DeliveryError, WorkflowState
from .evidence import EvidenceProof
from .qa import BrokerVerificationError, validate_broker_report


def accept_main_exception(
    engine: AutomationEngine, *, candidate_root: Path,
    broker_report: dict[str, Any], authorization: Mapping[str, Any],
    review: Mapping[str, Any],
) -> dict[str, Any]:
    spec = engine.spec
    binding = {"task_id": spec.task_id, "source_head": spec.source_head,
               "candidate_sha256": spec.candidate_sha256}
    expected_grant = {**binding, "grantor": "user", "scope": "stage3-engineering-only",
                      "allow_main_implementation": True, "allow_main_testing": True,
                      "allow_main_review": True, "instruction_sha256": authorization.get("instruction_sha256")}
    digest = expected_grant["instruction_sha256"]
    if (dict(authorization) != expected_grant or not isinstance(digest, str)
            or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
        raise DeliveryError("Explicit task/candidate-bound user authorization is required")
    expected_review = {**binding, "actor": "GPT main controller", "independent": False,
                       "status": "APPROVED", "review_type": "final", "simulation": False}
    if dict(review) != expected_review:
        raise DeliveryError("A truthful final main-controller review is required")
    state = engine._state()
    if state.get("status") != WorkflowState.TASK_CREATED.value:
        raise DeliveryError("Exception cannot replace prior evidence or reopen a failed task")
    try:
        verified = validate_broker_report(broker_report, candidate_root=candidate_root,
                                         task_id=spec.task_id, source_head=spec.source_head,
                                         expected_inventory_sha256=spec.candidate_sha256)
    except BrokerVerificationError:
        state["status"] = WorkflowState.QA_FAILED.value
        state["ready"] = False
        engine._save(state)
        raise
    if verified.get("status") != "PASS":
        raise DeliveryError("Actual broker QA has not passed")
    actual_commands = tuple(row["command"] for row in broker_report["suites"]
                            if row.get("process", {}).get("exit_code") == 0)
    if actual_commands != spec.expected_qa_commands:
        raise DeliveryError("Prescribed commands must match actual successful broker processes")
    sha = lambda value: hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    proof = EvidenceProof(task_id=spec.task_id, candidate_sha256=spec.candidate_sha256,
                          controller_sha256=sha(dict(authorization)),
                          qa_sha256=broker_report["receipt_sha256"], reviewer_sha256=sha(dict(review)),
                          commands=spec.expected_qa_commands, simulated=False,
                          acceptance_mode="main-authorized-exception",
                          authorization_sha256=sha(dict(authorization)))
    state["evidence"] = proof.as_dict()
    state["status"] = WorkflowState.QA_REVIEW_VERIFIED.value
    state["main_authorization"] = dict(authorization)
    engine._record_engine_event(state, None, "main_exception_verified", {
        "evidence": proof.as_dict(), "reviewer_independent": False,
    })
    return engine._save(state)
