"""Offline monitoring backup/recovery. Recovery never overwrites an existing home."""
import argparse
from pathlib import Path
from .store import MonitorStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "restore", "check"))
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    if args.action == "restore":
        if not args.backup:
            parser.error("--backup required")
        store = MonitorStore.restore(args.backup, args.home)
    else:
        store = MonitorStore(args.home)
    print(store.backup() if args.action == "backup" else "SCHEMA_1_OK")
    store.engine.dispose()


if __name__ == "__main__":
    main()
