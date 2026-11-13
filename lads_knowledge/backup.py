"""Consistent SQLite backup and new-alias restore operations."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .database import (
    APPLICATION_ID,
    HEAD_REVISION,
    DatabaseBoundaryError,
    FileStore,
    SchemaValidationError,
    _assert_private_namespace,
    _is_reparse,
    _open_readonly,
    create_memory_engine,
    database_path,
    read_application_id,
    validate_alias,
    validate_home,
    validate_schema,
    _assert_no_sidecars,
    _assert_sidecars_safe,
)
from .safety import canonical_json, utc_now_iso


class SQLiteConnection:
    """Small explicit wrapper around SQLite's online backup API."""

    def __init__(self, source: sqlite3.Connection) -> None:
        self.source = source

    def backup(self, destination: sqlite3.Connection) -> None:
        self.source.backup(destination)


@dataclass(slots=True)
class MemoryBackup:
    payload: bytes
    manifest: dict[str, Any]

    def _validated_copy(self) -> sqlite3.Connection:
        digest = hashlib.sha256(self.payload).hexdigest()
        if digest != self.manifest.get("sha256"):
            raise SchemaValidationError("Memory backup checksum does not match")
        if len(self.payload) < 100 or not self.payload.startswith(b"SQLite format 3\x00"):
            raise SchemaValidationError("Memory backup is not a complete SQLite database")
        app_id = int.from_bytes(self.payload[68:72], "big")
        if app_id != APPLICATION_ID:
            raise SchemaValidationError("Memory backup belongs to another application")
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            connection.deserialize(self.payload)
            validate_schema(connection, expected_revision=HEAD_REVISION, integrity=True)
            return connection
        except Exception:
            connection.close()
            raise

    def validate(self) -> dict[str, Any]:
        connection = self._validated_copy()
        connection.close()
        return dict(self.manifest)

    def restore(self) -> Any:
        """Restore into a new in-memory store without touching any file."""

        from .service import KnowledgeStore

        source = self._validated_copy()
        engine = create_memory_engine(migrate=False)
        raw = engine.raw_connection()
        try:
            driver = raw.driver_connection
            source.backup(driver)
            validate_schema(driver, expected_revision=HEAD_REVISION, integrity=True)
            raw.commit()
        except Exception:
            raw.rollback()
            raw.close()
            engine.dispose()
            source.close()
            raise
        raw.close()
        source.close()
        return KnowledgeStore(engine, kind="memory")


@dataclass(slots=True)
class FileBackup:
    home: Path
    path: Path
    manifest: dict[str, Any]

    def validate(self) -> dict[str, Any]:
        _validate_backup_file(self.home, self.path)
        return dict(self.manifest)

    def restore(self, target_alias: str) -> Any:
        return _restore_file_backup(self.home, self.path, target_alias)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_directory(home: Path) -> Path:
    directory = home / "backups"
    if directory.exists():
        if _is_reparse(directory) or not directory.is_dir():
            raise DatabaseBoundaryError("Backup directory is not a safe directory")
    else:
        directory.mkdir(mode=0o700)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    return directory


def _manifest_path(backup_path: Path) -> Path:
    return backup_path.with_suffix(".manifest.json")


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    payload = canonical_json(manifest) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _validate_backup_file(home: Path, path: Path) -> None:
    backup_dir = home.resolve(strict=True) / "backups"
    resolved = path.resolve(strict=True)
    if backup_dir.resolve(strict=True) not in resolved.parents:
        raise DatabaseBoundaryError("Backup must be inside the private backup directory")
    if _is_reparse(path):
        raise DatabaseBoundaryError("Backup file is a reparse point")
    _assert_sidecars_safe(path)
    if read_application_id(path) != APPLICATION_ID:
        raise DatabaseBoundaryError("Backup belongs to another application")
    manifest_path = _manifest_path(path)
    if not manifest_path.is_file() or _is_reparse(manifest_path):
        raise DatabaseBoundaryError("Backup manifest is missing or unsafe")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DatabaseBoundaryError("Backup manifest is unreadable") from exc
    if manifest.get("sha256") != _sha256_file(path):
        raise DatabaseBoundaryError("Backup checksum mismatch")
    if manifest.get("application_id") != APPLICATION_ID:
        raise DatabaseBoundaryError("Backup manifest application id is invalid")
    with _open_readonly(path) as connection:
        validate_schema(connection, expected_revision=HEAD_REVISION, integrity=True)


def _memory_backup(store: Any) -> MemoryBackup:
    with store._write_lock:
        raw = store._engine.raw_connection()
        destination = sqlite3.connect(":memory:")
        try:
            driver = raw.driver_connection
            SQLiteConnection(driver).backup(destination)
            destination.execute("PRAGMA foreign_keys = ON")
            revision = validate_schema(
                destination, expected_revision=HEAD_REVISION, integrity=True
            )
            payload = destination.serialize()
        finally:
            raw.rollback()
            raw.close()
            destination.close()
    manifest = {
        "application_id": APPLICATION_ID,
        "created_at": utc_now_iso(),
        "format": "lads-knowledge-memory-backup-v1",
        "schema_revision": revision,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }
    return MemoryBackup(payload, manifest)


def _file_backup(store: Any) -> FileBackup:
    home = validate_home(store.home)
    _assert_private_namespace(home)
    backup_dir = _backup_directory(home)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_dir / f"kb-{stamp}.sqlite3"
    if backup_path.exists():
        raise DatabaseBoundaryError("Backup alias already exists")
    with store._write_lock:
        raw = store._engine.raw_connection()
        destination = sqlite3.connect(backup_path)
        try:
            source = SQLiteConnection(raw.driver_connection)
            source.backup(destination)
            destination.execute("PRAGMA foreign_keys = ON")
            revision = validate_schema(
                destination, expected_revision=HEAD_REVISION, integrity=True
            )
            destination.commit()
        except Exception:
            destination.close()
            raw.rollback()
            raw.close()
            backup_path.unlink(missing_ok=True)
            raise
        finally:
            raw.rollback()
            raw.close()
            try:
                destination.close()
            except sqlite3.Error:
                pass
    try:
        backup_path.chmod(0o600)
    except OSError:
        pass
    manifest = {
        "application_id": APPLICATION_ID,
        "alias": backup_path.name,
        "created_at": utc_now_iso(),
        "format": "lads-knowledge-file-backup-v1",
        "schema_revision": revision,
        "sha256": _sha256_file(backup_path),
        "size": backup_path.stat().st_size,
    }
    _write_manifest(_manifest_path(backup_path), manifest)
    return FileBackup(home, backup_path, manifest)


def create_backup(store: Any) -> MemoryBackup | FileBackup:
    if store.kind == "memory":
        return _memory_backup(store)
    if store.kind == "file":
        return _file_backup(store)
    raise DatabaseBoundaryError("Unknown store kind")


def _restore_file_backup(home: Path, backup_path: Path, target_alias: str) -> Any:
    selected_home = validate_home(home)
    _assert_private_namespace(selected_home)
    _validate_backup_file(selected_home, backup_path)
    alias = validate_alias(target_alias)
    target = database_path(selected_home, alias)
    if target.exists() or _is_reparse(target):
        raise DatabaseBoundaryError("Restore target already exists")
    _assert_no_sidecars(target)

    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    owned = os.fstat(descriptor)
    os.close(descriptor)
    source = None
    destination = None
    try:
        source = _open_readonly(backup_path)
        destination = sqlite3.connect(target)
        source.backup(destination)
        destination.execute("PRAGMA foreign_keys = ON")
        validate_schema(
            destination, expected_revision=HEAD_REVISION, integrity=True
        )
        destination.commit()
        destination.close()
        try:
            target.chmod(0o600)
        except OSError:
            pass
        from .service import KnowledgeStore

        return FileStore.open(selected_home, alias)
    except Exception:
        try:
            if destination is not None:
                destination.close()
        except sqlite3.Error:
            pass
        if target.exists():
            current = target.stat()
            if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                target.unlink()
        raise
    finally:
        if source is not None:
            source.close()
