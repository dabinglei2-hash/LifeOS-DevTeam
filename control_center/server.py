"""Fixed-loopback Control Center server entrypoint."""

from __future__ import annotations

import argparse
import re
import uuid
from pathlib import Path

import uvicorn

from .api import DEFAULT_FRONTEND_DIST, create_app


def _port(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= parsed <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return parsed


def _instance(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value) is None:
        raise argparse.ArgumentTypeError("instance ID is invalid")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=_port, default=8765)
    parser.add_argument("--instance-id", type=_instance, default=None)
    parser.add_argument("--frontend-dist", type=Path, default=DEFAULT_FRONTEND_DIST)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    instance_id = args.instance_id or str(uuid.uuid4())
    app = create_app(
        frontend_dist=args.frontend_dist,
        port=args.port,
        instance_id=instance_id,
    )
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=args.port,
        proxy_headers=False,
        server_header=False,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
