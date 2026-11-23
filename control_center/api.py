"""Local-only FastAPI shell for the read-only LADS Control Center."""

from __future__ import annotations

import logging
import os
import re
import stat
import uuid
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from .telemetry import ROOT, SnapshotService

logger = logging.getLogger(__name__)
SERVICE_ID = "lads-control-center"
SCHEMA_ID = "stage1"
PROJECT_FILTERS = frozenset({"all", "lifeos", "devteam"})
DEFAULT_FRONTEND_DIST = ROOT.parent / "LADS-Workspace" / "control-center-data" / "frontend" / "dist"
CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)


def _generic_error(status_code: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": "Control Center request unavailable."}},
    )


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return True
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & marker)


def _allowed_host(host: str, port: int) -> bool:
    accepted = {
        f"127.0.0.1:{port}",
        f"localhost:{port}",
        f"[::1]:{port}",
    }
    return host.casefold() in accepted


def _allowed_origin(origin: str, port: int) -> bool:
    try:
        parsed = urlsplit(origin)
        parsed_port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed_port == port
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and parsed.path in {"", "/"}
    )


def _safe_file(root: Path, relative: str) -> Path | None:
    if not relative or "\\" in relative or ":" in relative:
        return None
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        return None
    try:
        root_resolved = root.resolve(strict=True)
    except OSError:
        return None
    if _is_reparse(root) or not root_resolved.is_dir():
        return None
    candidate = root_resolved.joinpath(*pure.parts)
    current = root_resolved
    for part in pure.parts:
        current = current / part
        if not current.exists() or _is_reparse(current):
            return None
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_relative_to(root_resolved) or not resolved.is_file():
        return None
    return resolved


def create_app(
    *,
    service: Any | None = None,
    frontend_dist: str | Path | None = None,
    port: int = 8765,
    instance_id: str | None = None,
) -> FastAPI:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Control Center port must be between 1 and 65535")
    selected_instance = instance_id or str(uuid.uuid4())
    if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", selected_instance) is None:
        raise ValueError("Control Center instance ID is invalid")
    selected_service = service or SnapshotService()
    dist = Path(frontend_dist) if frontend_dist is not None else DEFAULT_FRONTEND_DIST

    app = FastAPI(
        title="LADS Control Center",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.service = selected_service
    app.state.frontend_dist = dist
    app.state.port = port
    app.state.instance_id = selected_instance

    @app.middleware("http")
    async def local_security(request: Request, call_next: Any) -> Response:
        def secured(response: Response) -> Response:
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
            response.headers["Content-Security-Policy"] = CSP
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
            return response

        host = request.headers.get("host", "")
        if not _allowed_host(host, port):
            return secured(_generic_error(400, "INVALID_HOST"))
        origin = request.headers.get("origin")
        if origin is not None and not _allowed_origin(origin, port):
            return secured(_generic_error(403, "FOREIGN_ORIGIN"))
        if request.url.path.startswith("/api/"):
            if request.method != "GET":
                return secured(_generic_error(405, "READ_ONLY_API"))
            fetch_site = request.headers.get("sec-fetch-site", "").casefold()
            if fetch_site in {"cross-site", "same-site"}:
                return secured(_generic_error(403, "CROSS_SITE_REQUEST"))
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.error("Control Center request failed: %s", type(exc).__name__)
            return secured(_generic_error(500, "CONTROL_CENTER_UNAVAILABLE"))
        return secured(response)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "service": SERVICE_ID,
            "pid": os.getpid(),
            "instance_id": selected_instance,
            "read_only": True,
            "schema": SCHEMA_ID,
        }

    @app.get("/api/overview")
    def overview(project: str = Query(default="all")) -> Any:
        if project not in PROJECT_FILTERS:
            raise HTTPException(status_code=422, detail="Invalid project filter")
        try:
            payload = selected_service.snapshot(project=project)
        except Exception as exc:
            logger.error("Control Center snapshot failed: %s", type(exc).__name__)
            return _generic_error(503, "CONTROL_DATA_UNAVAILABLE")
        return JSONResponse(content=payload)

    @app.get("/")
    def index() -> Response:
        selected = _safe_file(dist, "index.html")
        if selected is None:
            return _generic_error(503, "UI_NOT_BUILT")
        return FileResponse(selected, media_type="text/html; charset=utf-8")

    @app.get("/assets/{asset_path:path}")
    def asset(asset_path: str) -> Response:
        selected = _safe_file(dist / "assets", asset_path)
        if selected is None:
            return _generic_error(404, "ASSET_NOT_FOUND")
        media_type = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".woff": "font/woff", ".woff2": "font/woff2"}.get(selected.suffix.lower(), "application/octet-stream")
        return FileResponse(selected, media_type=media_type)

    return app
