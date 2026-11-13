"""LADS Stage 3 engineering automation foundation.

This package coordinates engineering delivery metadata only. It does not read
product data, install hooks, run browser tests, or provide a network service.
"""
from __future__ import annotations

from .engine import (
    AutomationEngine,
    DeliveryError,
    DocumentPublisher,
    EVENT_DOCUMENTS,
    KNOWN_DOCUMENTS,
    KnowledgePublisher,
    MemoryPublisher,
    WorkflowState,
)
from .evidence import (
    ControllerReceipts,
    EvidenceError,
    EvidenceProof,
    QAEvidenceError,
    ReviewEvidenceError,
    TaskSpec,
    load_controller_receipts,
    receipt_digest,
    validate_stage_acceptance,
)
from .hooks import handle_hook
from .state import (
    FileJournal,
    JournalBoundaryError,
    JournalConflict,
    JournalError,
    JournalLockTimeout,
    JournalNotFound,
    MemoryJournal,
)

__all__ = [
    "AutomationEngine",
    "ControllerReceipts",
    "DeliveryError",
    "DocumentPublisher",
    "EVENT_DOCUMENTS",
    "EvidenceError",
    "EvidenceProof",
    "FileJournal",
    "JournalBoundaryError",
    "JournalConflict",
    "JournalError",
    "JournalLockTimeout",
    "JournalNotFound",
    "KNOWN_DOCUMENTS",
    "KnowledgePublisher",
    "MemoryJournal",
    "MemoryPublisher",
    "QAEvidenceError",
    "ReviewEvidenceError",
    "TaskSpec",
    "WorkflowState",
    "handle_hook",
    "load_controller_receipts",
    "receipt_digest",
    "validate_stage_acceptance",
]

__version__ = "0.1.0"
