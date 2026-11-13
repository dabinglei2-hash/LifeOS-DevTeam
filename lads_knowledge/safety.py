"""Input boundaries for the private LADS engineering knowledge store."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, unquote, urlsplit


MAX_SOURCE_REF = 1000
MAX_TEXT = 8_000
MAX_CONTEXT = 8_000


class KnowledgeError(ValueError):
    """Base class for rejected knowledge-store operations."""


class SensitiveReferenceError(KnowledgeError):
    """Raised when a stable identity would contain sensitive material."""


class NoisePayloadError(KnowledgeError):
    """Raised when metadata resembles raw dialogue, logs, or secrets."""


_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_AUTH = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|password|passwd|secret)"
    r"\s*[:=]\s*['\"]?[^\s,'\"]{6,}"
)
_COMMON_TOKEN = re.compile(r"\b(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{8,}\b")
_PHONE = re.compile(
    r"(?<![\w])(?:(?:\+?\d{1,3}[\s().-]+)?"
    r"(?:\(\d{2,4}\)|\d{2,4})[\s.-]+\d{3,4}[\s.-]+\d{3,4}"
    r"|\+?\d{10,15})(?![\w])"
)
_HEX_SHA = re.compile(r"\b[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?\b")
_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_KIND = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_FORBIDDEN_PAYLOAD_KEYS = {
    "completion",
    "content",
    "conversation",
    "dialogue",
    "log",
    "log_tail",
    "logs",
    "messages",
    "prompt",
    "raw_output",
    "response",
    "stderr",
    "stdout",
    "transcript",
}


def utc_now_iso() -> str:
    """Return a UTC ISO-8601 timestamp with an explicit ``Z`` suffix."""

    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalize_utc_iso(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value)).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KnowledgeError("Timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise KnowledgeError("Timestamp must include a UTC offset")
    return (
        parsed.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def normalize_kind(kind: str) -> str:
    value = unicodedata.normalize("NFKC", str(kind)).strip().lower()
    if not _KIND.fullmatch(value):
        raise KnowledgeError("Invalid knowledge kind")
    return value


def contains_sensitive_reference(value: str) -> bool:
    # Canonical UUID/Git/SHA-256 identity segments can contain long digit runs.
    # They are identifiers, not phone numbers; credentials/emails are still checked verbatim.
    phone_input = _HEX_SHA.sub("[SHA]", _UUID.sub("[UUID]", value))
    return bool(
        _EMAIL.search(value)
        or _PRIVATE_KEY.search(value)
        or _AUTH.search(value)
        or _SECRET_ASSIGNMENT.search(value)
        or _COMMON_TOKEN.search(value)
        or _PHONE.search(phone_input)
    )


def _strict_unquote_once(value: str) -> str:
    """Decode one UTF-8 percent layer and reject malformed escape triplets."""

    index = 0
    while index < len(value):
        if value[index] == "%":
            if (
                index + 2 >= len(value)
                or not re.fullmatch(r"[0-9A-Fa-f]{2}", value[index + 1 : index + 3])
            ):
                raise KnowledgeError("Source URL contains malformed percent encoding")
            index += 3
            continue
        index += 1
    try:
        return unquote(value, encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise KnowledgeError("Source URL percent encoding is not valid UTF-8") from exc


def _decode_percent_layers(value: str) -> str:
    """Fully decode bounded URL percent layers with strict UTF-8 semantics."""

    decoded = value
    for _ in range(8):
        next_value = _strict_unquote_once(decoded)
        if next_value == decoded:
            return decoded
        decoded = next_value
    raise KnowledgeError("Source URL has too many encoded layers")


def _normalize_url(value: str) -> str:
    # Validate every escape in the whole URL, including host, path, and rejected
    # components, before structural parsing can create identity collisions.
    _decode_percent_layers(value)
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise KnowledgeError("Source URL is invalid") from exc
    if parts.username is not None or parts.password is not None:
        raise SensitiveReferenceError("Source URLs may not contain user information")
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise KnowledgeError("Source URL must use http or https and include a host")
    if parts.query or parts.fragment:
        raise SensitiveReferenceError("Source URLs may not contain query or fragment data")
    host = unicodedata.normalize(
        "NFKC", _decode_percent_layers(parts.hostname)
    ).lower().rstrip(".")
    if not host:
        raise KnowledgeError("Source URL host is empty")
    if ":" in host and ":" not in parts.hostname:
        raise KnowledgeError("Source URL host contains an encoded separator")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise KnowledgeError("Source URL host is invalid") from exc
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError as exc:
            raise KnowledgeError("Source URL host is invalid") from exc
    elif not re.fullmatch(r"[a-z0-9.-]+", host):
        raise KnowledgeError("Source URL host is invalid")
    try:
        port = parts.port
    except ValueError as exc:
        raise KnowledgeError("Source URL port is invalid") from exc
    default_port = (parts.scheme.lower() == "http" and port == 80) or (
        parts.scheme.lower() == "https" and port == 443
    )
    host_for_url = f"[{host}]" if ":" in host else host
    netloc = (
        host_for_url
        if port is None or default_port
        else f"{host_for_url}:{port}"
    )
    path = parts.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    segments: list[str] = []
    for segment in path.split("/"):
        decoded_segment = unicodedata.normalize(
            "NFKC", _decode_percent_layers(segment)
        )
        if decoded_segment in {"", "."}:
            if not segments:
                continue
            continue
        if decoded_segment == "..":
            raise KnowledgeError("Source URL path may not contain dot-dot")
        if "/" in decoded_segment or "\\" in decoded_segment:
            raise KnowledgeError("Source URL path contains an encoded separator")
        segments.append(
            quote(decoded_segment, safe="!$&'()*+,;=:@-._~")
        )
    canonical_path = "/" + "/".join(segments)
    if path.endswith("/") and canonical_path != "/":
        canonical_path += "/"
    return f"{parts.scheme.lower()}://{netloc}{canonical_path}"


def _normalize_path(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    if "%" in value:
        raise KnowledgeError("Percent encoding is not supported in source paths")
    if any(ord(character) < 32 for character in value):
        raise KnowledgeError("Source path contains control characters")
    if value.startswith(("/", "\\", "//", "\\\\")):
        raise KnowledgeError("Source path must be relative")
    if re.match(r"^[A-Za-z]:", value):
        raise KnowledgeError("Drive-qualified source paths are not allowed")
    parts: list[str] = []
    for raw_part in re.split(r"[/\\]", value):
        normalized = unicodedata.normalize("NFKC", raw_part)
        if "/" in normalized or "\\" in normalized:
            raise KnowledgeError("Source path contains an encoded separator")
        if any(ord(character) < 32 for character in normalized):
            raise KnowledgeError("Source path contains control characters")
        if normalized == "..":
            raise KnowledgeError("Source path may not contain dot-dot")
        if normalized not in {"", "."}:
            parts.append(normalized)
    folded = "/".join(parts)
    if not folded:
        raise KnowledgeError("Source path is empty")
    if re.match(r"^[A-Za-z]:", folded):
        raise KnowledgeError("Drive-qualified source paths are not allowed")
    # Life OS development is hosted on Windows. Folding source paths avoids
    # duplicate identities for case-only variants while retaining exact text
    # in the separately sanitized display fields.
    return folded.casefold()


def normalize_source_ref(kind: str, ref: str) -> str:
    """Return the sole canonical source identity used by all store operations."""

    source_kind = normalize_kind(kind)
    value = unicodedata.normalize("NFKC", str(ref)).strip()
    if not value or len(value) > MAX_SOURCE_REF:
        raise KnowledgeError("Source reference is empty or too long")
    if any(ord(character) < 32 for character in value):
        raise KnowledgeError("Source reference contains control characters")

    if source_kind in {"url", "pull_request"}:
        decoded = unicodedata.normalize("NFKC", _decode_percent_layers(value))
        if contains_sensitive_reference(value) or contains_sensitive_reference(decoded):
            raise SensitiveReferenceError("Source reference contains sensitive data")
        return _normalize_url(value)
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value):
        raise KnowledgeError("Only HTTP(S) source URLs are supported")
    path_kinds = {"file", "path", "source", "code", "doc", "docs", "decision", "issue"}
    if source_kind in path_kinds or "/" in value or "\\" in value:
        if "%" in value:
            raise KnowledgeError("Percent encoding is not supported in source paths")
        if contains_sensitive_reference(value):
            raise SensitiveReferenceError("Source reference contains sensitive data")
        return _normalize_path(value)
    if contains_sensitive_reference(value):
        raise SensitiveReferenceError("Source reference contains sensitive data")
    if _HEX_SHA.fullmatch(value):
        return value.lower()
    if _UUID.fullmatch(value):
        return value.lower()
    return value


def normalize_uuid(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value)).strip().lower()
    if not _UUID.fullmatch(text):
        raise KnowledgeError("Value is not a UUID")
    return text


def normalize_sha(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", text):
        raise KnowledgeError("SHA must contain 40 or 64 hexadecimal characters")
    return text


def _sanitize_string(value: str, limit: int) -> str:
    text = unicodedata.normalize("NFKC", value)
    text = _PRIVATE_KEY.sub("[REDACTED_PRIVATE_KEY]", text)
    text = _AUTH.sub("[REDACTED_AUTH]", text)
    text = _SECRET_ASSIGNMENT.sub("[REDACTED_SECRET]", text)
    text = _COMMON_TOKEN.sub("[REDACTED_TOKEN]", text)
    text = _EMAIL.sub("[REDACTED_EMAIL]", text)
    text = _PHONE.sub("[REDACTED_PHONE]", text)
    if len(text) > limit:
        suffix = "…"
        text = text[: max(0, limit - len(suffix))] + suffix
    return text


def sanitize_text(value: Any, *, limit: int = MAX_TEXT, allow_empty: bool = True) -> str:
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        text = str(value)
    text = "".join(
        character
        if character in "\n\t" or ord(character) >= 32
        else " "
        for character in text
    )
    text = _sanitize_string(text, limit).strip()
    if not allow_empty and not text:
        raise KnowledgeError("Required text is empty")
    return text


def sanitize_metadata(
    value: Any,
    *,
    limit: int = 4_000,
    _depth: int = 0,
) -> Any:
    if _depth > 5:
        raise NoisePayloadError("Metadata nesting is too deep")
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = sanitize_text(raw_key, limit=80)
            if key.casefold() in _FORBIDDEN_PAYLOAD_KEYS:
                raise NoisePayloadError("Raw dialogue and log fields are not accepted")
            clean[key] = sanitize_metadata(raw_value, limit=limit, _depth=_depth + 1)
        encoded = canonical_json(clean)
        if len(encoded) > limit:
            raise NoisePayloadError("Metadata is too large")
        return clean
    if isinstance(value, (list, tuple)):
        items = [
            sanitize_metadata(item, limit=limit, _depth=_depth + 1)
            for item in value[:100]
        ]
        if len(items) != len(value):
            raise NoisePayloadError("Metadata list is too large")
        return items
    if isinstance(value, str):
        return sanitize_text(value, limit=limit)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise KnowledgeError("Non-finite numbers are not allowed")
        return value
    raise KnowledgeError("Metadata contains an unsupported value")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def bounded_context(value: dict[str, Any]) -> dict[str, Any]:
    """Keep returned search context below the hard 8 KiB safety ceiling."""

    encoded = canonical_json(value)
    if len(encoded.encode("utf-8")) <= MAX_CONTEXT:
        return value
    raise KnowledgeError("Response context exceeds the 8 KiB limit")

