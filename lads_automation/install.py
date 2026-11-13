"""Validation-only helpers for the reviewed Stage 3 protected install plan."""
from __future__ import annotations

import hashlib
import json
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from lads_automation.qa import source_inventory, inventory_digest

EXPECTED_TARGETS = (
    ".agents/skills/lifeos-data-guard/SKILL.md",
    ".agents/skills/lifeos-ios-design/SKILL.md",
    ".agents/skills/lifeos-project-memory/SKILL.md",
    ".agents/skills/lifeos-git-release/SKILL.md",
    ".agents/skills/lifeos-qa-testing/SKILL.md",
    ".agents/skills/lifeos-knowledge-sync/SKILL.md",
    ".agents/skills/lifeos-release-management/SKILL.md",
    "AGENTS.md",
    ".codex/hooks.json",
    "scripts/lads_orchestrator.py",
    "scripts/lifeos-deepseek.py",
    "tests/lads/test_orchestrator.py",
)
EXPECTED_CANDIDATE_PREFIXES = {
    ".agents/skills/lifeos-data-guard/SKILL.md": "engineering/lads-stage3-install/skills/lifeos-data-guard/SKILL.md",
    ".agents/skills/lifeos-ios-design/SKILL.md": "engineering/lads-stage3-install/skills/lifeos-ios-design/SKILL.md",
    ".agents/skills/lifeos-project-memory/SKILL.md": "engineering/lads-stage3-install/skills/lifeos-project-memory/SKILL.md",
    ".agents/skills/lifeos-git-release/SKILL.md": "engineering/lads-stage3-install/skills/lifeos-git-release/SKILL.md",
    ".agents/skills/lifeos-qa-testing/SKILL.md": "engineering/lads-stage3-install/skills/lifeos-qa-testing/SKILL.md",
    ".agents/skills/lifeos-knowledge-sync/SKILL.md": "engineering/lads-stage3-install/skills/lifeos-knowledge-sync/SKILL.md",
    ".agents/skills/lifeos-release-management/SKILL.md": "engineering/lads-stage3-install/skills/lifeos-release-management/SKILL.md",
    "AGENTS.md": "engineering/lads-stage3-install/AGENTS.md",
    ".codex/hooks.json": "engineering/lads-stage3-install/hooks.json",
    "scripts/lads_orchestrator.py": "engineering/lads-protocol-fix/lads_orchestrator.py",
    "scripts/lifeos-deepseek.py": "engineering/lads-protocol-fix/lifeos-deepseek.py",
    "tests/lads/test_orchestrator.py": "engineering/lads-stage3-install/tests/lads/test_orchestrator.py",
}
_SHA = re.compile(r"^[0-9a-f]{64}$")
_HEAD = re.compile(r"^[0-9a-f]{40,64}$")


class InstallPlanError(ValueError):
    """The reviewed install candidate is incomplete or drifted."""


@dataclass(frozen=True)
class InstallTarget:
    source_path: str
    candidate_path: str
    source_head: str
    original_sha256: str | None
    original_absent: bool
    candidate_sha256: str
    allowed_changes: tuple[str, ...]


@dataclass(frozen=True)
class ValidatedInstallPlan:
    source_head: str
    source_snapshot_sha256: str
    target_allowlist: tuple[str, ...]
    targets: tuple[InstallTarget, ...]
    map_sha256: str


INSTALL_MAP_PATH = "engineering/lads-stage3-install/install-map.json"


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_file() and not path.is_symlink() and not (
        getattr(info, "st_file_attributes", 0) & marker
    )


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if result.returncode != 0:
        raise InstallPlanError("Current Git HEAD could not be verified")
    return result.stdout.strip()


def candidate_snapshot_digest(root: str | Path, source_head: str) -> str:
    """Hash source inputs, normalizing only exact catalogued installed bytes.

    A worktree includes Git-visible source, while a detached candidate includes
    its complete inventory. Runtime logs/backups never become source inputs.
    """
    selected = Path(root).resolve(strict=True)
    files = _source_files(selected)
    files.pop(INSTALL_MAP_PATH, None)
    map_path = selected / INSTALL_MAP_PATH
    if _regular(map_path) and map_path.stat().st_size <= 256 * 1024:
        plan = json.loads(map_path.read_text(encoding="utf-8"))
        for target in plan.get("targets", []):
            path = target.get("source_path")
            candidate = target.get("candidate_path")
            if path not in EXPECTED_TARGETS or candidate != EXPECTED_CANDIDATE_PREFIXES.get(path):
                raise InstallPlanError("Installation normalization contains an unexpected target")
            digest = target.get("candidate_sha256")
            if files.get(path) == digest and files.get(candidate) == digest:
                if target.get("original_absent") is True:
                    files.pop(path, None)
                else:
                    files[path] = target["original_sha256"]
    return inventory_digest(source_head, files)


def _source_files(root: Path) -> dict[str, str]:
    probe = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, encoding="utf-8", timeout=30)
    if probe.returncode != 0 or Path(probe.stdout.strip()).resolve() != root:
        return source_inventory(root)
    listing = subprocess.run(["git", "-C", str(root), "ls-files", "--cached", "--others",
                              "--exclude-standard", "-z"], capture_output=True, timeout=30)
    if listing.returncode != 0 or len(listing.stdout) > 2 * 1024 * 1024:
        raise InstallPlanError("Source listing failed or exceeded its bound")
    files = {}
    for relative in listing.stdout.decode("utf-8").split("\0"):
        if not relative:
            continue
        _validate_relative(relative, "Source path")
        path = root / relative
        if not _regular(path) or not path.resolve().is_relative_to(root):
            raise InstallPlanError("Source listing contains an unsafe or missing file")
        for parent in path.parents:
            if parent == root:
                break
            if parent.is_symlink() or (getattr(parent.lstat(), "st_file_attributes", 0)
                                      & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
                raise InstallPlanError("Source listing traverses a reparse directory")
        files[relative] = _sha_file(path)
    return files


def _validate_relative(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 240
        or "\\" in value
        or value.startswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise InstallPlanError(f"{label} must be a bounded POSIX relative path")
    return value


def validate_install_plan(
    plan: dict[str, Any],
    *,
    root: str | Path,
    expected_head: str | None = None,
    expected_targets: Iterable[str] = EXPECTED_TARGETS,
) -> ValidatedInstallPlan:
    root = Path(root).resolve(strict=True)
    if not isinstance(plan, dict):
        raise InstallPlanError("Install plan must be an object")
    required = {
        "schema",
        "source_head",
        "source_snapshot_sha256",
        "target_allowlist",
        "targets",
    }
    if set(plan) != required or plan.get("schema") != "lads-stage3-install-map-v1":
        raise InstallPlanError("Install plan has an unsupported shape or schema")
    actual_head = _git_head(root)
    source_head = plan.get("source_head")
    if source_head != actual_head:
        raise InstallPlanError("Install plan source HEAD does not match the workspace")
    if expected_head is not None and source_head != expected_head:
        raise InstallPlanError("Install plan is bound to a different approved HEAD")
    snapshot = plan.get("source_snapshot_sha256")
    if not isinstance(snapshot, str) or not _SHA.fullmatch(snapshot):
        raise InstallPlanError("Source snapshot digest is malformed")
    if snapshot != candidate_snapshot_digest(root, source_head):
        raise InstallPlanError("Source snapshot digest does not bind the current candidate")
    allowlist = plan.get("target_allowlist")
    expected = tuple(expected_targets)
    if (
        not isinstance(allowlist, list)
        or len(allowlist) != len(expected)
        or set(allowlist) != set(expected)
        or len(set(allowlist)) != len(allowlist)
    ):
        raise InstallPlanError("Install target allowlist is not the exact reviewed set")
    rows = plan.get("targets")
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise InstallPlanError("Install plan must contain exactly twelve targets")
    targets: list[InstallTarget] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise InstallPlanError("Install target must be an object")
        fields = {
            "source_path",
            "candidate_path",
            "source_head",
            "original_sha256",
            "original_absent",
            "candidate_sha256",
            "allowed_changes",
        }
        if set(row) != fields:
            raise InstallPlanError("Install target fields are incomplete or unexpected")
        source = _validate_relative(row["source_path"], "source target")
        candidate = _validate_relative(row["candidate_path"], "candidate path")
        if source not in expected or source in seen:
            raise InstallPlanError("Install target is outside or duplicated in the allowlist")
        if candidate != EXPECTED_CANDIDATE_PREFIXES[source]:
            raise InstallPlanError("Install candidate path is not the reviewed fixed path")
        if row["source_head"] != source_head:
            raise InstallPlanError("Install target HEAD binding is inconsistent")
        absent = row["original_absent"]
        old = row["original_sha256"]
        new = row["candidate_sha256"]
        if type(absent) is not bool:
            raise InstallPlanError("original_absent must be boolean")
        if absent != (old is None):
            raise InstallPlanError("Original absence field and digest contradict")
        if old is not None and (not isinstance(old, str) or not _SHA.fullmatch(old)):
            raise InstallPlanError("Original target digest is malformed")
        if not isinstance(new, str) or not _SHA.fullmatch(new):
            raise InstallPlanError("Candidate digest is malformed")
        changes = row["allowed_changes"]
        if (
            not isinstance(changes, list)
            or not changes
            or len(changes) > 20
            or any(not isinstance(item, str) or not item or len(item) > 240 for item in changes)
            or len(set(changes)) != len(changes)
        ):
            raise InstallPlanError("Allowed changes are missing, duplicated, or unbounded")
        candidate_file = root / candidate
        if not _regular(candidate_file) or _sha_file(candidate_file) != new:
            raise InstallPlanError(f"Reviewed candidate bytes drifted: {candidate}")
        target_file = root / source
        actual_old = _sha_file(target_file) if _regular(target_file) else None
        if target_file.exists() and not _regular(target_file):
            raise InstallPlanError(f"Protected target is not a regular file: {source}")
        if actual_old not in (old, new):
            raise InstallPlanError(f"Current protected target drifted: {source}")
        targets.append(
            InstallTarget(
                source_path=source,
                candidate_path=candidate,
                source_head=row["source_head"],
                original_sha256=old,
                original_absent=absent,
                candidate_sha256=new,
                allowed_changes=tuple(changes),
            )
        )
        seen.add(source)
    if seen != set(expected):
        raise InstallPlanError("Install plan did not cover the exact target allowlist")
    return ValidatedInstallPlan(
        source_head=source_head,
        source_snapshot_sha256=snapshot,
        target_allowlist=expected,
        targets=tuple(sorted(targets, key=lambda item: item.source_path)),
        map_sha256=hashlib.sha256(
            json.dumps(
                plan,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    )


def load_install_plan(
    path: str | Path,
    *,
    root: str | Path,
    expected_head: str | None = None,
) -> ValidatedInstallPlan:
    selected = Path(path)
    if not _regular(selected):
        raise InstallPlanError("Install map must be a regular file")
    if selected.stat().st_size > 512 * 1024:
        raise InstallPlanError("Install map exceeds its bounded size")
    try:
        value = json.loads(selected.read_text(encoding="utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise InstallPlanError("Install map is not valid UTF-8 JSON") from exc
    return validate_install_plan(
        value,
        root=root,
        expected_head=expected_head,
    )


def summarize_skills_listing(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep only skill name/path/errors from an appserver forceReload result."""
    if not isinstance(response, dict):
        raise InstallPlanError("Skills listing must be an object")
    root = response.get("result", response)
    skills = root.get("skills") if isinstance(root, dict) else None
    if not isinstance(skills, list) or len(skills) > 500:
        raise InstallPlanError("Skills listing is missing or unbounded")
    summary: list[dict[str, Any]] = []
    for skill in skills:
        if not isinstance(skill, dict):
            raise InstallPlanError("Skills listing contains a malformed entry")
        name = skill.get("name")
        path = skill.get("path")
        errors = skill.get("errors", skill.get("error", []))
        if errors is None:
            errors = []
        if not isinstance(errors, list):
            errors = [errors]
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 200
            or not isinstance(path, str)
            or not path
            or len(path) > 1000
            or len(errors) > 20
            or any(not isinstance(item, str) or len(item) > 500 for item in errors)
        ):
            raise InstallPlanError("Skills listing entry has unsafe metadata")
        summary.append(
            {
                "name": name,
                "path": path,
                "errors": list(errors),
            }
        )
    return summary
