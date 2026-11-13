#!/usr/bin/env python3
"""Explicit DeepSeek worker processes. No GPT fallback. Python 3.11+."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tomllib
import uuid

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "scanner": "deepseek-flash",
    "reviewer": "deepseek-flash",
    "builder": "deepseek-flash",
    "qa-tester": "deepseek-flash",
}
KEY_ENV = {
    "scanner": "DEEPSEEK_SCANNER_API_KEY",
    "reviewer": "DEEPSEEK_REVIEWER_API_KEY",
    "builder": "DEEPSEEK_BUILDER_API_KEY",
    "qa-tester": "DEEPSEEK_QA_API_KEY",
}
PROVIDER = {
    "name": "DeepSeek",
    "base_url": "https://api.deepseek.com",
    "wire_api": "responses",
    "requires_openai_auth": False,
}
AUTHORITY_PROFILE_SUFFIX = "-deepseek.config.toml"
RUNTIME_HOMES_DIRECTORY = "runtime-homes"
HOME_MODES = {"shared", "frozen"}
PLATFORM_HOME_MODES = {"nt": "shared", "posix": "frozen"}
CONFIG_KEYS = {
    "model",
    "model_provider",
    "model_catalog_json",
    "model_reasoning_effort",
    "approval_policy",
    "sandbox_mode",
    "web_search",
    "notify",
    "windows",
    "model_providers",
    "agents",
    "features",
    "projects",
}
ORCHESTRATED_OUTPUT_PROTOCOL = (
    "ORCHESTRATED OUTPUT PROTOCOL: The parent orchestrator owns normalized run metadata. "
    "Your LAST assistant message must be one plain JSON object only, with exactly the "
    "task_id, role, status, summary, evidence fields required by the current role, plus "
    "source_snapshot_sha256 only for qa-tester. Do not add metadata, Markdown, prose, or "
    "any other fields. The strict parent parser is authoritative."
)


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _user_home():
    try:
        return Path.home()
    except RuntimeError:
        for name in ("HOME", "USERPROFILE"):
            value = os.environ.get(name)
            if value:
                return Path(value)
        codex_value = os.environ.get("CODEX_HOME")
        if codex_value:
            return Path(codex_value).resolve(strict=False).parent
    raise ValueError("user home is unavailable")


def codex_home(explicit=None):
    value = explicit
    if value is None:
        value = os.environ.get("CODEX_HOME")
    if value is None:
        value = _user_home() / ".codex"
    return Path(value).resolve(strict=False)


def platform_home_mode(platform_name=None):
    value = os.name if platform_name is None else platform_name
    require(value in PLATFORM_HOME_MODES, "unsupported worker platform")
    return PLATFORM_HOME_MODES[value]


def _same_path(left, right):
    return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))


def _is_reparse(path):
    path = Path(path)
    try:
        info = path.lstat()
    except OSError:
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _path_within(path, parent):
    path_value = os.path.normcase(os.path.abspath(str(Path(path).resolve(strict=False))))
    parent_value = os.path.normcase(os.path.abspath(str(Path(parent).resolve(strict=False))))
    try:
        return os.path.commonpath((path_value, parent_value)) == parent_value
    except ValueError:
        return False


def _require_regular_file(path, label):
    path = Path(path)
    require(path.is_absolute(), f"{label} must be absolute")
    require(path.is_file(), f"{label} is missing")
    require(not _is_reparse(path), f"{label} is a reparse/symlink")
    current = path.parent
    while True:
        require(not _is_reparse(current), f"{label} parent is a reparse/symlink")
        if current.parent == current:
            break
        current = current.parent
    return path


def _validate_project_candidate(path):
    raw = Path(path)
    require(raw.is_absolute(), "project root must be absolute")
    require(not re.search(r"[*?\[\]\x00]", str(raw)), "wildcard project root refused")
    current = raw
    while True:
        require(not _is_reparse(current), "reparse/symlink project root refused")
        if current.parent == current:
            break
        current = current.parent
    resolved = raw.resolve(strict=True)
    require(resolved.is_dir(), "project root must be an existing directory")
    require(not _same_path(resolved, _user_home().resolve(strict=False)), "user home project root refused")
    require(resolved.parent != resolved, "drive/root project root refused")
    return resolved


def _git_probe(root, *args):
    completed = subprocess.run(
        ["git", "-c", "safe.directory=" + str(root), "-C", str(root), "rev-parse", *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    require(completed.returncode == 0, "project root is not a verifiable Git repository")
    value = completed.stdout.strip()
    require(value and "\n" not in value, "Git project metadata is ambiguous")
    return value


def _validate_git_marker(root, git_dir):
    root, git_dir = Path(root), Path(git_dir)
    marker = root / ".git"
    require(marker.exists(), "Git repository marker is missing")
    require(not _is_reparse(marker), "Git repository marker is a reparse/symlink")
    if marker.is_dir():
        require(_same_path(marker.resolve(strict=True), git_dir), "Git directory marker mismatch")
        return None
    require(marker.is_file(), "Git repository marker is not a directory or gitdir file")
    require(marker.stat().st_size <= 4096, "Git repository marker is oversized")
    line = marker.read_text(encoding="utf-8").strip()
    match = re.fullmatch(r"(?i)gitdir:\s*(.+)", line)
    require(match is not None, "Git repository marker is malformed")
    registered = Path(match.group(1))
    if not registered.is_absolute():
        registered = root / registered
    require(_same_path(registered.resolve(strict=True), git_dir), "Git repository gitdir marker mismatch")
    return marker


def _git_metadata(root):
    root = _validate_project_candidate(root)
    top = Path(_git_probe(root, "--show-toplevel")).resolve(strict=True)
    git_dir = Path(_git_probe(root, "--absolute-git-dir")).resolve(strict=True)
    common_raw = Path(_git_probe(root, "--git-common-dir"))
    common = common_raw.resolve(strict=True) if common_raw.is_absolute() else (root / common_raw).resolve(strict=True)
    marker = _validate_git_marker(top, git_dir)
    require(common.is_dir(), "Git common directory is missing")
    require(not _is_reparse(common), "Git common directory is a reparse/symlink")
    if marker is not None:
        backlink = git_dir / "gitdir"
        require(backlink.is_file() and not _is_reparse(backlink), "linked worktree gitdir backlink is missing")
        require(backlink.stat().st_size <= 4096, "linked worktree gitdir backlink is oversized")
        target = Path(backlink.read_text(encoding="utf-8").strip())
        if not target.is_absolute():
            target = git_dir / target
        require(_same_path(target.resolve(strict=True), marker.resolve(strict=True)),
                "linked worktree gitdir backlink mismatch")
    common_root = _validate_project_candidate(common.parent)
    common_top = Path(_git_probe(common_root, "--show-toplevel")).resolve(strict=True)
    common_git_dir = Path(_git_probe(common_root, "--absolute-git-dir")).resolve(strict=True)
    common_again_raw = Path(_git_probe(common_root, "--git-common-dir"))
    common_again = (
        common_again_raw.resolve(strict=True)
        if common_again_raw.is_absolute()
        else (common_root / common_again_raw).resolve(strict=True)
    )
    _validate_git_marker(common_top, common_git_dir)
    require(_same_path(common_top, common_root), "Git common parent is not its repository root")
    require(_same_path(common_again, common), "Git common directory equivalence failed")
    return top, common_root


def authorized_project_roots(workspace):
    workspace = _validate_project_candidate(workspace)
    top, common_root = _git_metadata(workspace)
    roots = []
    seen = set()
    for candidate in (workspace, top, common_root):
        key = os.path.normcase(os.path.abspath(str(candidate)))
        if key not in seen:
            seen.add(key)
            roots.append(candidate)
    require(roots and all(path.is_absolute() and path.exists() and not _is_reparse(path) for path in roots),
            "authorized project roots are incomplete")
    return tuple(roots)


def _normalized_project_key(value):
    require(isinstance(value, str) and value, "project key must be a non-empty string")
    require(not re.search(r"[*?\[\]\x00]", value), "wildcard project key refused")
    raw_components = re.split(r"[\\/]+", value)
    require("." not in raw_components and ".." not in raw_components,
            "project key dot/path aliases are refused")
    raw = Path(value)
    require(raw.is_absolute(), "project key must be absolute")
    return os.path.normcase(os.path.abspath(str(raw)))


def _project_key_sha256(value):
    return hashlib.sha256(_normalized_project_key(value).encode("utf-8")).hexdigest()


def _canonical_sha256(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def main_config_snapshot(explicit_home=None):
    path = codex_home(explicit_home) / "config.toml"
    if not path.exists():
        return {
            "present": False,
            "sha256": None,
            "project_entries": {},
            "project_keys": {},
            "non_projects_sha256": _canonical_sha256({}),
        }
    path = _require_regular_file(path, "main Codex config")
    raw = path.read_bytes()
    require(len(raw) <= 4 * 1024 * 1024, "main Codex config is oversized")
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    data = tomllib.loads(raw.decode("utf-8-sig"))
    require(isinstance(data, dict), "main Codex config root must be a table")
    projects = data.get("projects", {})
    require(isinstance(projects, dict), "main Codex projects must be a table")
    entries = {}
    key_identities = {}
    for key, value in projects.items():
        key_sha256 = _project_key_sha256(key)
        require(key_sha256 not in entries, "main Codex project keys alias or collide")
        require(isinstance(value, dict), "main Codex project entry must be a table")
        entries[key_sha256] = _canonical_sha256(value)
        key_identities[key_sha256] = _canonical_sha256(key)
    non_projects = {key: value for key, value in data.items() if key != "projects"}
    return {
        "present": True,
        "sha256": raw_sha256,
        "project_entries": entries,
        "project_keys": key_identities,
        "non_projects_sha256": _canonical_sha256(non_projects),
    }


def main_config_transition(expected, actual, allowed_project_roots=()):
    expected_fields = {
        "present",
        "sha256",
        "project_entries",
        "project_keys",
        "non_projects_sha256",
    }
    require(isinstance(expected, dict) and set(expected) == expected_fields,
            "expected main Codex config snapshot is malformed")
    require(isinstance(actual, dict) and set(actual) == expected_fields,
            "actual main Codex config snapshot is malformed")
    for label, value in (("expected", expected), ("actual", actual)):
        require(type(value["present"]) is bool, f"{label} main Codex presence is malformed")
        require(
            (value["present"] is False and value["sha256"] is None)
            or (
                value["present"] is True
                and isinstance(value["sha256"], str)
                and re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
            ),
            f"{label} main Codex raw SHA is malformed",
        )
        require(
            isinstance(value["non_projects_sha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", value["non_projects_sha256"]),
            f"{label} main Codex non-project hash is malformed",
        )
    expected_projects = expected["project_entries"]
    actual_projects = actual["project_entries"]
    expected_keys = expected["project_keys"]
    actual_keys = actual["project_keys"]
    require(isinstance(expected_projects, dict) and isinstance(actual_projects, dict),
            "main Codex project snapshot is malformed")
    require(isinstance(expected_keys, dict) and isinstance(actual_keys, dict),
            "main Codex project-key snapshot is malformed")
    require(all(isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key)
                and isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                for key, value in expected_projects.items()),
            "expected main Codex project snapshot is malformed")
    require(all(isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key)
                and isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                for key, value in actual_projects.items()),
            "actual main Codex project snapshot is malformed")
    require(all(isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key)
                and isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                for key, value in expected_keys.items()),
            "expected main Codex project-key snapshot is malformed")
    require(all(isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key)
                and isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                for key, value in actual_keys.items()),
            "actual main Codex project-key snapshot is malformed")
    require(set(expected_keys) == set(expected_projects)
            and set(actual_keys) == set(actual_projects),
            "main Codex project-key snapshot is inconsistent")
    require(expected["non_projects_sha256"] == actual["non_projects_sha256"],
            "non-project main Codex config changed")

    removed = sorted(set(expected_projects) - set(actual_projects))
    changed = sorted(
        key for key in set(expected_projects) & set(actual_projects)
        if expected_projects[key] != actual_projects[key]
    )
    require(not removed, "existing main Codex project entry was removed")
    require(not changed, "existing main Codex project entry changed")
    renamed = sorted(
        key for key in set(expected_keys) & set(actual_keys)
        if expected_keys[key] != actual_keys[key]
    )
    require(not renamed, "existing main Codex project key spelling changed")

    trusted_value_sha256 = _canonical_sha256({"trust_level": "trusted"})
    allowed = {}
    for root in allowed_project_roots:
        root = Path(root)
        require(root.is_absolute() and root.is_dir() and not _is_reparse(root),
                "allowed project root is not an absolute regular directory")
        normalized = _normalized_project_key(str(root.resolve(strict=True)))
        key_sha256 = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        require(key_sha256 not in allowed, "allowed project roots alias or collide")
        allowed[key_sha256] = normalized
    added = sorted(set(actual_projects) - set(expected_projects))
    if expected["sha256"] == actual["sha256"]:
        require(not added, "same-byte main Codex config has inconsistent project metadata")
        status = "UNCHANGED"
        added_roots = []
    else:
        require(added, "main Codex config raw SHA changed without an allowed project addition")
        for key_sha256 in added:
            require(key_sha256 in allowed, "unknown main Codex project entry was added")
            require(actual_projects[key_sha256] == trusted_value_sha256,
                    "new main Codex project entry is not exactly trusted")
        status = "ALLOWED_TRUST_REGISTRATION"
        added_roots = [allowed[key_sha256] for key_sha256 in added]
    return {
        "status": status,
        "before_present": expected["present"],
        "before_sha256": expected["sha256"],
        "after_present": actual["present"],
        "after_sha256": actual["sha256"],
        "allowed_project_root_names": [str(Path(root).resolve(strict=True)) for root in allowed_project_roots],
        "added_project_root_names": added_roots,
    }


def sandbox_mode(role):
    return "workspace-write" if role == "builder" else "read-only"


def key_present(role):
    return bool(os.environ.get(KEY_ENV[role]))


def _route_from_profile(role, home, profile):
    profile = _require_regular_file(profile, "profile")
    d = tomllib.loads(profile.read_text(encoding="utf-8-sig"))
    providers = d.get("model_providers", {})
    provider = providers.get("lifeos_deepseek", {}) if isinstance(providers, dict) else {}
    require(d.get("model_provider") == "lifeos_deepseek" and d.get("model") == MODELS[role], "profile route mismatch")
    require(provider.get("base_url") == PROVIDER["base_url"] and provider.get("wire_api") == PROVIDER["wire_api"], "endpoint mismatch")
    require(provider.get("env_key") == KEY_ENV[role]
            and provider.get("requires_openai_auth") is PROVIDER["requires_openai_auth"],
            "auth route mismatch")
    require(not any(k in provider for k in ("experimental_bearer_token", "auth")), "unsafe profile auth")
    mode = sandbox_mode(role)
    require(d.get("sandbox_mode") == mode and d.get("approval_policy") == "never", "permission mismatch")
    raw_catalog = d.get("model_catalog_json")
    require(isinstance(raw_catalog, str) and raw_catalog, "catalog path missing")
    catalog = Path(raw_catalog)
    if not catalog.is_absolute():
        catalog = profile.parent / catalog
    catalog = _require_regular_file(catalog.resolve(strict=True), "catalog")
    require(MODELS[role] in {m["slug"] for m in json.loads(catalog.read_text(encoding="utf-8"))["models"]},
            "catalog mismatch")
    return profile, catalog, mode


def check(role, explicit_home=None, *, platform_name=None):
    require(role in MODELS, "unknown role")
    mode = platform_home_mode(platform_name)
    if mode == "shared":
        require(explicit_home is None, "shared Windows home cannot be caller supplied")
        home = codex_home()
    else:
        home = codex_home(explicit_home)
    profile = home / (role + AUTHORITY_PROFILE_SUFFIX)
    profile, catalog, sandbox = _route_from_profile(role, home, profile)
    return home, profile, catalog, sandbox


def authority_snapshot(role, explicit_home=None, *, platform_name=None):
    home, profile, catalog, _mode = check(role, explicit_home, platform_name=platform_name)
    return {
        "home_mode": platform_home_mode(platform_name),
        "original_codex_home": str(home),
        "profile_sha256": digest(profile),
        "catalog_sha256": digest(catalog),
    }


def authority_inventory(explicit_home=None, *, platform_name=None):
    values = {
        role: authority_snapshot(role, explicit_home, platform_name=platform_name)
        for role in sorted(MODELS)
    }
    homes = {item["original_codex_home"].casefold() for item in values.values()}
    modes = {item["home_mode"] for item in values.values()}
    require(len(homes) == 1, "profile authority does not share one Codex home")
    require(len(modes) == 1, "profile authority has mixed home modes")
    return values


def _toml_string(value):
    return json.dumps(str(value), ensure_ascii=True)


def _runtime_config(role, home, catalog, roots):
    mode = sandbox_mode(role)
    values = [
        "model=" + _toml_string(MODELS[role]),
        'model_provider="lifeos_deepseek"',
        "model_catalog_json=" + _toml_string(catalog),
        'model_reasoning_effort="high"',
        'approval_policy="never"',
        "sandbox_mode=" + _toml_string(mode),
        'web_search="disabled"',
        "notify=[]",
        "",
        "[windows]",
        'sandbox="elevated"',
        "",
    ]
    for root in roots:
        values.extend([
            "[projects." + _toml_string(os.path.normcase(str(root))) + "]",
            'trust_level="trusted"',
            "",
        ])
    values.extend([
        "[model_providers.lifeos_deepseek]",
        "name=" + _toml_string(PROVIDER["name"]),
        "base_url=" + _toml_string(PROVIDER["base_url"]),
        "wire_api=" + _toml_string(PROVIDER["wire_api"]),
        "env_key=" + _toml_string(KEY_ENV[role]),
        "requires_openai_auth=false",
        "",
        "[agents]",
        "enabled=false",
        "",
        "[features]",
        "apps=false",
        "plugins=false",
        "",
    ])
    return "\n".join(values)


def _validate_runtime_config(path, role, catalog, roots):
    data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    require(set(data) == CONFIG_KEYS, "runtime config contains non-whitelisted sections")
    require(data.get("model") == MODELS[role] and data.get("model_provider") == "lifeos_deepseek",
            "runtime route mismatch")
    require(data.get("model_catalog_json") == str(catalog), "runtime catalog binding mismatch")
    require(data.get("model_reasoning_effort") == "high", "runtime reasoning setting mismatch")
    require(data.get("approval_policy") == "never", "runtime approval mismatch")
    require(data.get("sandbox_mode") == sandbox_mode(role), "runtime sandbox mismatch")
    require(data.get("web_search") == "disabled", "runtime web search is enabled")
    require(data.get("notify") == [], "runtime notify list is not empty")
    require(data.get("windows") == {"sandbox": "elevated"}, "runtime Windows sandbox mismatch")
    provider = data.get("model_providers", {}).get("lifeos_deepseek")
    require(provider == {
        "name": PROVIDER["name"],
        "base_url": PROVIDER["base_url"],
        "wire_api": PROVIDER["wire_api"],
        "env_key": KEY_ENV[role],
        "requires_openai_auth": False,
    }, "runtime provider mismatch")
    require(data.get("agents") == {"enabled": False}, "runtime native agents are not disabled")
    require(data.get("features") == {"apps": False, "plugins": False}, "runtime connectors are not disabled")
    expected_projects = {
        os.path.normcase(str(root)): {"trust_level": "trusted"}
        for root in roots
    }
    require(data.get("projects") == expected_projects, "runtime project trust tables mismatch")
    text = path.read_text(encoding="utf-8")
    require("hooks" not in data and "mcp_servers" not in data, "runtime hooks/MCP sections are forbidden")
    require("auth.json" not in text and "experimental_bearer_token" not in text,
            "runtime config contains credential paths")
    return data


def _runtime_parent(run_root):
    run_root = Path(run_root).resolve(strict=True)
    require(run_root.is_dir(), "runtime run root is missing")
    require(not _is_reparse(run_root), "runtime run root is a reparse/symlink")
    current = run_root
    while True:
        require(not _is_reparse(current), "runtime run-root parent is a reparse/symlink")
        if current.parent == current:
            break
        current = current.parent
    container = run_root / RUNTIME_HOMES_DIRECTORY
    if container.exists():
        require(container.is_dir() and not _is_reparse(container),
                "runtime-home container is unsafe")
    else:
        container.mkdir(exist_ok=False)
    require(_path_within(container, run_root), "runtime-home container escapes run root")
    return container


def validate_runtime_home(run_root, home, *, user_home=None, original_home=None):
    run_root = Path(run_root).resolve(strict=True)
    candidate = Path(home)
    require(candidate.is_absolute(), "runtime home must be absolute")
    container = run_root / RUNTIME_HOMES_DIRECTORY
    require(os.path.normcase(str(candidate.parent.resolve(strict=False)))
            == os.path.normcase(str(container.resolve(strict=False))),
            "runtime home is not the expected run-root child")
    require(uuid.UUID(candidate.name).version in {1, 2, 3, 4, 5},
            "runtime home directory is not a fresh UUID")
    require(not candidate.exists(), "runtime home already exists")
    current = candidate.parent
    while True:
        require(not _is_reparse(current), "runtime home parent is a reparse/symlink")
        if current.parent == current:
            break
        current = current.parent
    user_home = Path(user_home or _user_home()).resolve(strict=False)
    original_home = Path(original_home or codex_home()).resolve(strict=False)
    resolved = candidate.resolve(strict=False)
    require(os.path.normcase(str(resolved)) == os.path.normcase(str(candidate)),
            "runtime home resolves through an alias")
    require(not _same_path(resolved, user_home), "runtime home equals the user home")
    require(not _same_path(resolved, original_home), "runtime home equals the original CODEX_HOME")
    existing_sensitive = {
        original_home / "config.toml",
        original_home / "auth.json",
        original_home / "sessions",
        original_home / "session",
    }
    require(not any(_same_path(resolved, path) for path in existing_sensitive),
            "runtime home aliases an existing config/auth/session path")
    require(_path_within(resolved, run_root), "runtime home escapes the approved run root")
    return candidate


def create_runtime_home(role, workspace, run_root, *, platform_name=None):
    require(role in MODELS, "unknown role")
    require(platform_home_mode(platform_name) == "frozen",
            "runtime home creation is only supported in frozen mode")
    original_home, _profile, source_catalog, _mode = check(
        role,
        platform_name=platform_name,
    )
    roots = authorized_project_roots(workspace)
    container = _runtime_parent(run_root)
    home = container / str(uuid.uuid4())
    validate_runtime_home(run_root, home)
    os.mkdir(home)
    require(not _is_reparse(home), "runtime home became a reparse/symlink")
    require(_path_within(home.resolve(strict=True), Path(run_root).resolve(strict=True)),
            "created runtime home escaped its run root")
    catalog = home / "models.json"
    config = home / "config.toml"
    profile = home / (role + AUTHORITY_PROFILE_SUFFIX)
    catalog_bytes = source_catalog.read_bytes()
    with catalog.open("xb") as stream:
        stream.write(catalog_bytes)
    payload = _runtime_config(role, home, catalog, roots)
    with config.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
    with profile.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
    _validate_runtime_config(config, role, catalog, roots)
    _validate_runtime_config(profile, role, catalog, roots)
    require(digest(catalog) == digest(source_catalog), "runtime catalog copy changed bytes")
    return {
        "role": role,
        "home_mode": "frozen",
        "home": home,
        "config": config,
        "profile": profile,
        "catalog": catalog,
        "project_roots": [str(root) for root in roots],
        "original_home": original_home,
        "authority_sha256": hashlib.sha256(
            json.dumps(
                authority_snapshot(role),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }


def shared_home_binding(role, source_repo, *, platform_name=None):
    require(platform_home_mode(platform_name) == "shared",
            "shared home binding requires Windows shared mode")
    home, profile, catalog, mode = check(role, platform_name=platform_name)
    require(home.is_dir() and not _is_reparse(home), "original Codex home is unsafe")
    roots = authorized_project_roots(source_repo)
    return {
        "role": role,
        "home_mode": "shared",
        "home": home,
        "config": home / "config.toml",
        "profile": profile,
        "catalog": catalog,
        "sandbox_mode": mode,
        "project_roots": [str(root) for root in roots],
        "original_home": home,
        "authority_sha256": _canonical_sha256(authority_snapshot(role, platform_name=platform_name)),
    }


def runtime_binding(role, source_repo, run_root, *, platform_name=None):
    mode = platform_home_mode(platform_name)
    if mode == "shared":
        return shared_home_binding(role, source_repo, platform_name=platform_name)
    return create_runtime_home(
        role,
        source_repo,
        run_root,
        platform_name=platform_name,
    )


def _project_configuration(workspace, roots, *, excluded_paths=()):
    top = roots[1] if len(roots) > 1 else roots[0]
    current = Path(workspace).resolve()
    configs = []
    while _path_within(current, top):
        candidate = current / ".codex" / "config.toml"
        require(
            not any(_same_path(candidate, excluded) for excluded in excluded_paths),
            "project config aliases the mutable main Codex config",
        )
        if candidate.exists():
            candidate = _require_regular_file(candidate, "project config")
            data = tomllib.loads(candidate.read_text(encoding="utf-8-sig"))
            require(not data.get("hooks"), "project hooks require separate security review")
            configs.append((candidate, data))
        if _same_path(current, top):
            break
        current = current.parent
    return tuple(configs)


def _global_mcp_names_from_data(data):
    """Return only validated global MCP names, never their configuration values."""
    servers = data.get("mcp_servers", {}) if isinstance(data, dict) else None
    require(isinstance(servers, dict), "global MCP configuration must be a table")
    names = []
    for name in servers:
        require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_-]+", name),
                "unsupported global MCP identifier")
        names.append(name)
    return tuple(sorted(names))


def _global_mcp_names(home):
    config = Path(home) / "config.toml"
    if not config.exists():
        return ()
    config = _require_regular_file(config, "global Codex config")
    data = tomllib.loads(config.read_text(encoding="utf-8-sig"))
    return _global_mcp_names_from_data(data)


def command(
    role,
    workspace,
    explicit_home=None,
    *,
    source_repo=None,
    platform_name=None,
):
    home_mode = platform_home_mode(platform_name)
    home, profile, catalog, mode = check(
        role,
        explicit_home,
        platform_name=platform_name,
    )
    roots = authorized_project_roots(source_repo or workspace)
    executable = shutil.which("codex")
    require(executable, "codex not found")
    # Explicit route overrides protect against accidental GPT inheritance.
    a = [executable, "--no-daemon", "-C", str(workspace), "--profile", role + "-deepseek",
         "--sandbox", mode, "--ask-for-approval", "never", "--disable", "apps", "--disable", "plugins",
         "--disable", "multi_agent", "--disable", "multi_agent_v2"]
    values = {
        "model_provider": "lifeos_deepseek",
        "model": MODELS[role],
        "agents.enabled": False,
        "web_search": "disabled",
        "features.apps": False,
        "features.plugins": False,
        "sandbox_workspace_write.writable_roots": [],
        "sandbox_workspace_write.network_access": False,
        "sandbox_workspace_write.exclude_tmpdir_env_var": True,
        "sandbox_workspace_write.exclude_slash_tmp": True,
        "shell_environment_policy.exclude": ["*KEY*", "*TOKEN*", "*SECRET*", "*PASSWORD*"],
    }
    project_configs = _project_configuration(
        workspace,
        roots,
        excluded_paths=(home / "config.toml",),
    )
    global_names = _global_mcp_names(codex_home())
    for name in global_names:
        values["mcp_servers." + name + ".enabled"] = False
    for _config, data in project_configs:
        for name in data.get("mcp_servers", {}):
            require(re.fullmatch(r"[A-Za-z0-9_-]+", name), "unsupported MCP identifier")
            values["mcp_servers." + name + ".enabled"] = False
    instructions = (ROOT / ".codex/worker-prompts" / (role + ".md")).read_text(encoding="utf-8")
    require("DeepSeek" in instructions and ("read-only" in instructions or role == "builder"),
            "role prompt lacks isolated-process guidance")
    values["developer_instructions"] = instructions.rstrip() + "\n\n" + ORCHESTRATED_OUTPUT_PROTOCOL
    for key, value in values.items():
        a.extend(["-c", key + "=" + json.dumps(value, ensure_ascii=True)])
    # Frozen runtime homes contain only the selected role. The immutable route
    # authority for all four roles remains the original user home.
    authority = authority_inventory(platform_name=platform_name)
    snapshots = {
        "runtime:home_mode": _canonical_sha256(home_mode),
        str(profile): digest(profile),
        str(catalog): digest(catalog),
    }
    if home_mode == "frozen":
        snapshots[str(home / "config.toml")] = digest(home / "config.toml")
    snapshots.update({str(path): digest(path) for path, _data in project_configs})
    global_config = codex_home() / "config.toml"
    # Shared Windows homes are guarded by main_config_transition(), which
    # permits only exact trust registrations. A raw-file snapshot here would
    # reject those legitimate registrations before that stronger guard runs.
    if home_mode != "shared" and global_config.is_file():
        snapshots[str(global_config)] = digest(global_config)
    for authority_role, item in authority.items():
        for role_name, value in item.items():
            if role_name.endswith("_sha256"):
                snapshots[f"authority:{authority_role}:{role_name}"] = value
    prompt = ROOT / ".codex/worker-prompts" / (role + ".md")
    snapshots[str(prompt)] = digest(prompt)
    snapshots[str(Path(__file__).resolve())] = digest(Path(__file__).resolve())
    executable_path = Path(executable)
    if executable_path.is_file():
        snapshots[str(executable_path)] = digest(executable_path)
    return a, snapshots


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("role", choices=MODELS)
    p.add_argument("--check", action="store_true", help="offline profile/command validation only")
    p.add_argument("--workspace", type=Path, default=ROOT)
    p.add_argument("--codex-home", type=Path, help="explicit profile home for offline checks")
    args = p.parse_args()
    workspace = args.workspace.resolve()
    explicit_home = args.codex_home.resolve() if args.codex_home is not None else None
    argv, snapshots = command(args.role, workspace, explicit_home)
    require(args.check, "Live dispatch is gated until DeepSeek API/tool/permission acceptance; see docs/CODEX_MODEL_ROUTING.md")
    print(json.dumps({"status": "OFFLINE_CHECK_PASS", "role": args.role,
                      "provider": "lifeos_deepseek", "model": MODELS[args.role],
                      "sandbox": sandbox_mode(args.role),
                      "profile": args.role + "-deepseek", "api_tested": False,
                      "key_env": KEY_ENV[args.role], "key_present": key_present(args.role),
                      "config_files_checked": len(snapshots)}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, tomllib.TOMLDecodeError) as e:
        print("BLOCKED: " + (str(e) if isinstance(e, ValueError) else type(e).__name__), file=sys.stderr)
        sys.exit(2)
