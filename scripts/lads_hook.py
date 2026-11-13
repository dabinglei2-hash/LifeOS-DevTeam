#!/usr/bin/env python3
"""Bounded native-hook entrypoint for the project-local LADS candidate."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lads_automation.hooks import MAX_HOOK_INPUT_BYTES, handle_hook
from lads_automation.state import FileJournal, JournalError, acceptance_root, default_automation_root

_SESSION_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_WARNING = "LADS journal exists but is invalid; no task state was selected or created."


def _safe_session(value: Any) -> str | None:
    if not isinstance(value, str) or not _SESSION_RE.fullmatch(value):
        return None
    return value


def _strict_payload(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_HOOK_INPUT_BYTES:
        raise ValueError("Hook input exceeds its bounded size")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("Hook input contains duplicate keys")
            result[key] = value
        return result

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("Hook input contains non-JSON numeric constants")
            ),
        )
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Hook input is not strict UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Hook input must be an object")
    return parsed


def _journal_and_warning() -> tuple[FileJournal | None, str | None]:
    try:
        # A native acceptance process selects an owned synthetic journal
        # without changing LOCALAPPDATA used by the Windows sandbox runtime.
        namespace = os.environ.get("LADS_HOOK_ACCEPTANCE_NAMESPACE")
        root = acceptance_root(namespace) if namespace else default_automation_root()
        if not root.exists():
            return None, None
        journal = FileJournal.open(acceptance_namespace=namespace) if namespace else FileJournal.open()
        return journal, None
    except JournalError:
        return None, _WARNING


def _bounded(value: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) <= 4096:
        return value
    return {
        "systemMessage": "LADS hook output exceeded its bound and was suppressed.",
        "hookSpecificOutput": {
            "hookEventName": str(value.get("hookSpecificOutput", {}).get("hookEventName", "")),
            "additionalContext": "Bounded hook warning; inspect local hook diagnostics.",
        },
    }


def _with_warning(result: dict[str, Any], warning: str, event: str) -> dict[str, Any]:
    merged = dict(result)
    hook_output = dict(merged.get("hookSpecificOutput") or {})
    hook_output.setdefault("hookEventName", event)
    existing = hook_output.get("additionalContext")
    hook_output["additionalContext"] = (
        f"{existing} {warning}"[:1000] if existing else warning
    )
    merged["hookSpecificOutput"] = hook_output
    merged["systemMessage"] = warning
    return merged


def main() -> int:
    payload = _strict_payload(sys.stdin.buffer.read(MAX_HOOK_INPUT_BYTES + 1))
    if "session_id" not in payload:
        session = _safe_session(os.environ.get("CODEX_THREAD_ID"))
        if session is not None:
            payload["session_id"] = session
    journal, warning = _journal_and_warning()
    result = handle_hook(payload, journal)
    if warning:
        result = _with_warning(
            result,
            warning,
            str(
                payload.get("hook_event_name")
                or payload.get("event_name")
                or payload.get("hook_event")
                or ""
            ),
        )
    print(json.dumps(_bounded(result), ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        message = str(exc).replace("\r", " ").replace("\n", " ")[:240]
        print(
            json.dumps(
                {
                    "systemMessage": "LADS hook input rejected: " + message,
                    "hookSpecificOutput": {
                        "hookEventName": "",
                        "additionalContext": "Bounded hook input validation failure.",
                    },
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        raise SystemExit(1)
