"""Pure native-hook handlers.

These handlers are defensive guards, not a sandbox or complete enforcement
boundary. Native hook installation and trust are intentionally not performed by
this module.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
import uuid
from pathlib import Path
from typing import Any

from .state import JournalSessionAmbiguous, JournalSessionCorrupt

MAX_HOOK_INPUT_BYTES = 64 * 1024
MAX_HOOK_OUTPUT_BYTES = 4096

_SEGMENT_SPLIT = re.compile(r"(?:&&|\|\||;|\r?\n)")
_STORAGE_CODE = re.compile(
    r"\b(?:localStorage|sessionStorage)\s*\.\s*(?:clear|removeItem|setItem)\s*\("
    r"|\bindexedDB\s*\.\s*(?:deleteDatabase|openDatabase)\s*\(",
    re.IGNORECASE,
)
_PRIVATE_NAMES = (
    ".env",
    "auth.json",
    "credentials",
    "credential.json",
    "notion.sqlite",
    "notion.db",
)
_PRIVATE_NAME = re.compile(
    r"(?i)(?<![a-z0-9_.-])"
    r"(?:\.env|auth\.json|credentials|credential\.json|notion\.sqlite3?|notion\.db)"
    r"(?![a-z0-9_.-])"
)
_PRIVATE_WRITE_PROGRAMS = {
    "add-content",
    "clear-content",
    "copy",
    "copy-item",
    "cp",
    "del",
    "erase",
    "install",
    "ln",
    "move",
    "move-item",
    "mv",
    "new-item",
    "out-file",
    "remove",
    "remove-item",
    "rename",
    "rename-item",
    "rm",
    "set-content",
    "tee",
    "tee-object",
    "touch",
}
_SQL_WRITE = re.compile(
    r"\b(?:alter|attach|create|delete|drop|insert|pragma|reindex|replace|"
    r"restore|update|vacuum)\b",
    re.IGNORECASE,
)
_REDIRECT = re.compile(
    r"(?<![0-9])>>?\s*(?P<target>\"[^\"]*\"|'[^']*'|[^\s;&|]+)",
)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason[:300],
        }
    }


def _review_context(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": f"LADS review required: {reason}"[:300],
        }
    }


def _binding_warning(event: str) -> dict[str, Any]:
    message = (
        "LADS task binding is ambiguous or unreadable; no task state was selected "
        "and no automatic continuation was requested."
    )
    return {
        "systemMessage": message,
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": message,
        },
    }


def _corruption_result(event: str, error: JournalSessionCorrupt) -> dict[str, Any]:
    warning = error.warning()
    additional = (
        warning["message"]
        + f" Unreadable task count: {warning['unreadable_task_count']}."
    )
    if event == "PreToolUse":
        result = _deny(additional)
        result["hookSpecificOutput"]["additionalContext"] = additional
        return result
    return {
        "systemMessage": additional,
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": additional,
        },
    }


def _task_for_payload(payload: dict[str, Any], journal: Any) -> str | None:
    if journal is None:
        return None
    session = payload.get("session_id")
    if not isinstance(session, str) or not 1 <= len(session) <= 200:
        return None
    resolved = journal.resolve_session(session)
    if resolved is None:
        return None
    candidate = payload.get("task_id")
    if isinstance(candidate, str):
        try:
            identity = str(uuid.UUID(candidate))
        except (ValueError, TypeError):
            return None
        if identity != resolved:
            return None
        return identity
    return resolved


def _session_binding(payload: dict[str, Any]) -> str:
    session = str(payload.get("session_id") or "")
    if not session:
        return ""
    return hashlib.sha256(session.encode("utf-8")).hexdigest()


def _append_once(
    journal: Any,
    task_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    suffix: str = "",
) -> bool:
    row = _bounded_append(
        journal,
        task_id,
        event_type,
        payload,
        suffix=suffix,
    )
    return bool(row is not None and row.get("idempotent") is False)


def _warn_stop_gate(journal: Any, task_id: str, payload: dict[str, Any]) -> None:
    _bounded_append(
        journal,
        task_id,
        "stop_gate_warning",
        {
            "ready": False,
            "session_binding_sha256": _session_binding(payload),
            "message": "Delivery gate incomplete; no further automatic continuation",
        },
        suffix=_session_binding(payload),
    )


def _bounded_append(
    journal: Any,
    task_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    suffix: str = "",
) -> dict[str, Any] | None:
    if task_id is None:
        return None
    identity = str(uuid.uuid5(uuid.UUID(task_id), f"hook|{event_type}|{suffix}"))
    return journal.append_event(task_id, identity, event_type, payload)


def _command_segments(command: str) -> list[list[str]]:
    segments: list[list[str]] = []
    for segment in _SEGMENT_SPLIT.split(command):
        segment = segment.strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            tokens = segment.split()
        while tokens and tokens[0].casefold() in {"sudo", "doas", "command"}:
            tokens = tokens[1:]
        segments.append(tokens)
    return segments


def _git_analysis(tokens: list[str]) -> tuple[list[str] | None, str | None]:
    """Return Git arguments and a review warning for unknown global options."""
    if not tokens:
        return None, None
    program = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if program not in {"git", "git.exe"}:
        return None, None

    value_options = {
        "-C",
        "-c",
        "--attr-source",
        "--config-env",
        "--exec-path",
        "--git-dir",
        "--namespace",
        "--super-prefix",
        "--work-tree",
    }
    flag_options = {
        "-P",
        "-p",
        "--bare",
        "--glob-pathspecs",
        "--help",
        "--icase-pathspecs",
        "--literal-pathspecs",
        "--no-advice",
        "--no-lazy-fetch",
        "--no-optional-locks",
        "--no-pager",
        "--no-replace-objects",
        "--noglob-pathspecs",
        "--paginate",
        "--version",
    }
    position = 1
    while position < len(tokens):
        token = tokens[position].casefold()
        if token == "--":
            position += 1
            break
        if token in value_options:
            if position + 1 >= len(tokens):
                return None, "Git command has a global option without a value"
            position += 2
            continue
        if token in flag_options:
            position += 1
            continue
        if token.startswith("--") and "=" in token:
            option = token.split("=", 1)[0]
            if option in value_options:
                position += 1
                continue
        if (token.startswith("-C") or token.startswith("-c")) and len(token) > 2:
            position += 1
            continue
        if token.startswith("-"):
            return None, "Git command uses an unrecognized global option"
        break
    return tokens[position:] or None, None


def _git_subcommand(tokens: list[str]) -> list[str] | None:
    """Return Git arguments after the executable and supported global options."""
    return _git_analysis(tokens)[0]


def _mentions_private_path(value: str) -> bool:
    return _PRIVATE_NAME.search(value) is not None


def _private_write(command: str) -> tuple[str, str] | None:
    for match in _REDIRECT.finditer(command):
        target = match.group("target").strip("\"'")
        if _mentions_private_path(target):
            return "deny", "Credential or private business database writes are forbidden"
    for tokens in _command_segments(command):
        if not tokens:
            continue
        program = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].casefold()
        if program in {"bash", "cmd", "powershell", "pwsh", "sh", "zsh"}:
            for position, token in enumerate(tokens[1:], start=1):
                if token.casefold() in {"/c", "/k", "-c", "-command", "-lc"}:
                    nested = _private_write(" ".join(tokens[position + 1 :]))
                    if nested:
                        return nested
        if program in _PRIVATE_WRITE_PROGRAMS and any(
            _mentions_private_path(token) for token in tokens[1:]
        ):
            return "deny", "Credential or private business database writes are forbidden"
        if program in {"sqlite", "sqlite3"} and any(
            _mentions_private_path(token) for token in tokens[1:]
        ):
            sql = " ".join(tokens[1:])
            if _SQL_WRITE.search(sql):
                return "deny", "Business database mutation is forbidden"
    if any(
        marker in command.casefold()
        for marker in (
            "open(",
            "write_text(",
            "write_bytes(",
            "sqlite3.connect(",
            "executescript(",
            "execute(",
        )
    ):
        for tokens in _command_segments(command):
            if not tokens:
                continue
            program = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].casefold()
            if program in {
                "bun",
                "deno",
                "node",
                "nodejs",
                "perl",
                "php",
                "py",
                "python",
                "python3",
                "ruby",
            } and any(_mentions_private_path(token) for token in tokens):
                return "deny", "Command code writes a credential or private business database"
    return None


def _private_reference_review(command: str) -> str | None:
    for tokens in _command_segments(command):
        if not tokens:
            continue
        program = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].casefold()
        if program in {"echo", "printf"} and not _REDIRECT.search(command):
            continue
        if any(_mentions_private_path(token) for token in tokens):
            return "Command mentions a credential or private database path"
    return None


def _dangerous_command(command: str) -> str | None:
    lowered = command.casefold()
    for tokens in _command_segments(command):
        folded = [token.casefold() for token in tokens]
        if not folded:
            continue
        if folded[0] in {"bash", "sh", "zsh", "pwsh", "powershell"}:
            for marker in ("-c", "-command", "-lc", "-c "):
                if marker in folded:
                    position = folded.index(marker)
                    if position + 1 < len(tokens):
                        nested = _dangerous_command(" ".join(tokens[position + 1 :]))
                        if nested:
                            return nested
        if folded[0] == "cmd" and len(folded) >= 3 and folded[1] in {"/c", "/k"}:
            nested = _dangerous_command(" ".join(tokens[2:]))
            if nested:
                return nested
        git_arguments = _git_subcommand(tokens)
        if git_arguments:
            git_folded = [token.casefold() for token in git_arguments]
            if git_folded[0] == "reset" and "--hard" in git_folded[1:]:
                return "git reset --hard is outside the LADS Stage 3 boundary"
            if git_folded[0] == "clean":
                return "git clean is outside the LADS Stage 3 boundary"
            if git_folded[0] == "push":
                return "git push is outside the LADS Stage 3 boundary"
            if git_folded[0] == "merge":
                return "git merge is outside the LADS Stage 3 boundary"
        if folded[0] in {"rm", "remove-item"} and any("auth.json" in item or ".env" in item for item in folded[1:]):
            return "Deleting credentials is forbidden"
        if folded[0] in {"wrangler", "vercel", "netlify"} and any(item in {"deploy", "publish"} for item in folded[1:]):
            return "Deployment commands are outside the LADS Stage 3 boundary"
        if folded[0] in {"npm", "pnpm", "yarn"} and "publish" in folded[1:]:
            return "Package publication is outside the LADS Stage 3 boundary"
        if folded[:3] == ["gh", "pr", "merge"] or folded[:3] == ["gh", "pr", "create"]:
            return "GitHub PR mutation is outside the LADS Stage 3 boundary"
        if folded[0] in {"kubectl", "terraform"} and any(
            item in {"apply", "delete", "destroy", "rollout"} for item in folded[1:]
        ):
            return "Infrastructure deployment is outside the LADS Stage 3 boundary"

    program = ""
    for tokens in _command_segments(command):
        if tokens:
            program = tokens[0].casefold()
            break
    if program in {"node", "nodejs", "bun", "deno"} or (
        program in {"python", "python3", "py"} and any(token in {"-c", "-"} for token in command.split())
    ):
        if _STORAGE_CODE.search(command):
            return "Direct browser-storage mutation is forbidden"
    if "clear-site-data" in lowered and program not in {"echo", "printf"}:
        return "Direct browser-storage clearing is forbidden"
    return None


def _command_review(command: str) -> str | None:
    """Surface commands whose safety cannot be classified by the bounded parser."""
    for tokens in _command_segments(command):
        if not tokens:
            continue
        folded = [token.casefold() for token in tokens]
        if folded[0] in {"bash", "sh", "zsh", "pwsh", "powershell"}:
            for marker in ("-c", "-command", "-lc"):
                if marker in folded:
                    position = folded.index(marker)
                    if position + 1 < len(tokens):
                        nested = _command_review(" ".join(tokens[position + 1 :]))
                        if nested:
                            return nested
        if folded[0] == "cmd" and len(folded) >= 3 and folded[1] in {"/c", "/k"}:
            nested = _command_review(" ".join(tokens[2:]))
            if nested:
                return nested
        _arguments, warning = _git_analysis(tokens)
        if warning:
            return warning
    return None


def _path_risks(tool_input: dict[str, Any], payload: dict[str, Any]) -> tuple[str | None, str | None]:
    raw_path = tool_input.get("path") or tool_input.get("file_path") or tool_input.get("filename")
    if not isinstance(raw_path, str) or not raw_path:
        return None, None
    if "\x00" in raw_path or len(raw_path) > 1024:
        return "Path input is unbounded or contains NUL", None
    lowered = raw_path.replace("\\", "/").casefold()
    if any(name in lowered for name in _PRIVATE_NAMES):
        return "Credential or private business database paths are forbidden", None
    if any(part == ".." for part in Path(raw_path.replace("\\", "/")).parts):
        return "Path traversal is not allowed", None
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd:
        try:
            root = Path(cwd).resolve(strict=False)
            selected = Path(raw_path)
            if not selected.is_absolute():
                selected = root / selected
            selected = selected.resolve(strict=False)
        except OSError:
            return None, "Path containment could not be verified"
        if selected != root and root not in selected.parents:
            return "Write path escapes the current workspace", None
    return None, None


def _pre_tool_use(payload: dict[str, Any], journal: Any = None) -> dict[str, Any]:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return {}
    tool_name = str(payload.get("tool_name") or "").casefold()
    command = tool_input.get("command") or tool_input.get("cmd")
    if isinstance(command, str) and len(command) <= MAX_HOOK_INPUT_BYTES:
        private_write = _private_write(command)
        if private_write and private_write[0] == "deny":
            return _deny(private_write[1])
        reason = _dangerous_command(command)
        if reason:
            return _deny(reason)
        command_review = _command_review(command)
        if command_review:
            return _review_context(command_review)
        private_review = _private_reference_review(command)
        if private_review:
            return _review_context(private_review)
    if "browser" in tool_name:
        browser_request = " ".join(
            str(value)
            for key, value in tool_input.items()
            if key.casefold() in {"action", "operation", "expression", "script"}
        ).casefold()
        if any(
            marker in browser_request
            for marker in ("clear", "delete_database", "deleteDatabase".casefold(), "clear-site-data")
        ):
            return _deny("Direct browser-storage mutation is forbidden")
    path_deny, review = _path_risks(tool_input, payload)
    if path_deny:
        return _deny(path_deny)
    if review:
        return _review_context(review)
    patch = tool_input.get("patch") or tool_input.get("input")
    if isinstance(patch, str) and len(patch) <= MAX_HOOK_INPUT_BYTES:
        if any(name in patch.casefold() for name in (".env", "auth.json", "credentials")):
            return _review_context("Patch text mentions sensitive paths")
    if journal is not None and isinstance(payload.get("session_id"), str):
        _task_for_payload(payload, journal)
    return {}


def _exit_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    response = payload.get("tool_response")
    exit_code: int | None = None
    if isinstance(response, dict):
        raw_exit = response.get("exit_code", response.get("exitCode"))
        if type(raw_exit) is int and -255 <= raw_exit <= 255:
            exit_code = raw_exit
    digest_source = response if response is not None else {}
    try:
        digest = hashlib.sha256(_canonical(digest_source).encode("utf-8")).hexdigest()
    except (TypeError, ValueError):
        digest = None
    if exit_code is None:
        status = "unknown"
    elif exit_code == 0:
        status = "completed"
    else:
        status = "failed"
    return {"exit_code": exit_code, "status": status, "response_sha256": digest}


def _post_tool_use(payload: dict[str, Any], journal: Any) -> dict[str, Any]:
    task_id = _task_for_payload(payload, journal)
    if task_id is None:
        return {}
    metadata = _exit_metadata(payload)
    metadata["native_event"] = True
    metadata["session_binding_sha256"] = _session_binding(payload)
    _bounded_append(
        journal,
        task_id,
        "hook_post_tool",
        metadata,
        suffix=str(payload.get("tool_use_id") or metadata.get("response_sha256") or ""),
    )
    return {}


def _subagent_stop(payload: dict[str, Any], journal: Any) -> dict[str, Any]:
    agent_type = str(payload.get("agent_type") or "").casefold()
    provider = str(payload.get("model_provider") or payload.get("provider") or "").casefold()
    role = str(payload.get("role") or "").casefold()
    event_source = str(payload.get("event_source") or "").casefold()
    if (
        "deepseek" in agent_type
        or "deepseek" in provider
        or "deepseek" in role
        or payload.get("worker_type") == "cli"
        or event_source in {"controller", "deepseek", "subprocess"}
    ):
        return {}
    task_id = _task_for_payload(payload, journal)
    if task_id is None:
        return {}
    agent_identity = str(payload.get("agent_id") or agent_type)
    binding = _session_binding(payload)
    safe_suffix = hashlib.sha256(
        f"{agent_identity}|{binding}".encode("utf-8")
    ).hexdigest()
    _bounded_append(
        journal,
        task_id,
        "native_subagent_stop",
        {
            "native_event": True,
            "agent_type": agent_type[:100] or "native",
            "status": "stopped",
            "session_binding_sha256": binding,
        },
        suffix=safe_suffix,
    )
    return {}


def _delivery_complete(state: dict[str, Any]) -> bool:
    evidence = state.get("evidence")
    documents = state.get("documents_receipt")
    index = state.get("index_receipt")
    return bool(
        state.get("ready") is True
        and state.get("status") == "DELIVERY_READY"
        and isinstance(evidence, dict)
        and evidence.get("qa_review_verified") is True
        and evidence.get("whole_task_complete") is False
        and isinstance(documents, dict)
        and isinstance(index, dict)
        and index.get("working_content_commit") is None
        and index.get("index_event_id")
    )


def _stop(payload: dict[str, Any], journal: Any) -> dict[str, Any]:
    task_id = _task_for_payload(payload, journal)
    if task_id is None:
        return {}
    state = journal.get_task(task_id)
    if _delivery_complete(state):
        return {}
    binding = _session_binding(payload)
    if payload.get("stop_hook_active") is True:
        _warn_stop_gate(journal, task_id, payload)
        return {}
    requested = _append_once(
        journal,
        task_id,
        "stop_continuation_requested",
        {
            "ready": False,
            "attempt": 1,
            "session_binding_sha256": binding,
            "message": "One bounded continuation may be requested",
        },
        suffix=binding,
    )
    if requested:
        return {
            "decision": "block",
            "reason": (
                "LADS delivery gate is incomplete. Complete documentation and "
                "index verification."
            ),
        }
    _warn_stop_gate(journal, task_id, payload)
    return {}


def _small_output(result: dict[str, Any]) -> dict[str, Any]:
    if len(_canonical(result).encode("utf-8")) > MAX_HOOK_OUTPUT_BYTES:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": "LADS hook result exceeded its output bound",
            }
        }
    return result


def handle_hook(payload: dict[str, Any], journal: Any | None = None) -> dict[str, Any]:
    """Process one verified native-hook payload without persisting raw content."""
    if not isinstance(payload, dict):
        return {}
    try:
        encoded = _canonical(payload).encode("utf-8")
    except (TypeError, ValueError):
        return {}
    if len(encoded) > MAX_HOOK_INPUT_BYTES:
        return _deny("Hook input exceeds the bounded 64 KiB limit")
    event = payload.get("hook_event_name") or payload.get("event_name") or payload.get("hook_event")
    event = str(event or "").strip()
    try:
        if event == "PreToolUse":
            result = _pre_tool_use(payload, journal)
        elif event == "PostToolUse":
            result = _post_tool_use(payload, journal)
        elif event == "SubagentStop":
            result = _subagent_stop(payload, journal)
        elif event == "Stop":
            result = _stop(payload, journal)
        else:
            result = {}
    except JournalSessionAmbiguous:
        result = _binding_warning(event)
    except JournalSessionCorrupt as error:
        result = _corruption_result(event, error)
    return _small_output(result)
