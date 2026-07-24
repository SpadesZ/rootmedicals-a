"""
模組定位: llmebm SQLite 資料庫的離線備份、完整性驗證與受控還原工具。
主要責任: 使用 sqlite3 原生 backup API 產生一致快照，並以 integrity_check 與 SHA-256 擋住損壞檔。
呼叫來源: 維運人員在 stop service 後執行的 deployment/rollback/DR runbook。
輸入契約: 明確的 SQLite source/backup/target 路徑；restore 需 expected SHA-256，覆寫需 --replace。
輸出契約: stdout JSON receipt，包含來源、目的地、digest、bytes、integrity 與 pre-restore backup。
安全邊界: 不備份外部網路、不讀 secrets；restore 不會無條件覆寫既有 DB。
維護提醒: 線上 restore 前必須停止 writer；VM 排程、加密與 retention 由部署層負責。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def verify_database(path: str | Path) -> dict[str, object]:
    database = Path(path).resolve()
    if not database.is_file():
        raise ValueError(f"SQLite database does not exist: {database}")
    uri = database.as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        rows = [row[0] for row in connection.execute("PRAGMA integrity_check").fetchall()]
    if rows != ["ok"]:
        raise ValueError("SQLite integrity_check failed: " + "; ".join(rows[:10]))
    return {
        "database": str(database),
        "sha256": sha256_file(database),
        "bytes": database.stat().st_size,
        "integrity": "ok",
        "verified_at": _utc_now(),
    }


def backup_database(source: str | Path, destination: str | Path) -> dict[str, object]:
    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if not source_path.is_file():
        raise ValueError(f"SQLite source does not exist: {source_path}")
    if source_path == destination_path:
        raise ValueError("Source and destination must be different")
    if destination_path.exists():
        raise ValueError(f"Backup destination already exists: {destination_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = destination_path.with_name(destination_path.name + ".partial")
    if partial_path.exists():
        raise ValueError(f"Partial backup already exists: {partial_path}")

    source_uri = source_path.as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(source_uri, uri=True)) as source_connection:
            with closing(sqlite3.connect(partial_path)) as destination_connection:
                source_connection.backup(destination_connection)
        verify_database(partial_path)
        os.replace(partial_path, destination_path)
    except Exception:
        partial_path.unlink(missing_ok=True)
        raise

    receipt = verify_database(destination_path)
    receipt.update({"operation": "backup", "source": str(source_path), "destination": str(destination_path)})
    return receipt


def restore_database(
    backup: str | Path,
    target: str | Path,
    *,
    expected_sha256: str,
    replace: bool = False,
) -> dict[str, object]:
    backup_path = Path(backup).resolve()
    target_path = Path(target).resolve()
    backup_receipt = verify_database(backup_path)
    if backup_receipt["sha256"] != expected_sha256:
        raise ValueError("Backup SHA-256 does not match --expected-sha256")
    if backup_path == target_path:
        raise ValueError("Backup and target must be different")
    if target_path.exists() and not replace:
        raise ValueError("Target exists; pass --replace after stopping the service")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    pre_restore = None
    if target_path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        pre_restore_path = target_path.with_name(f"{target_path.stem}.pre-restore-{stamp}{target_path.suffix}")
        pre_restore = backup_database(target_path, pre_restore_path)

    partial_target = target_path.with_name(target_path.name + ".restore-partial")
    if partial_target.exists():
        raise ValueError(f"Partial restore already exists: {partial_target}")
    try:
        with closing(sqlite3.connect(backup_path.as_uri() + "?mode=ro", uri=True)) as source_connection:
            with closing(sqlite3.connect(partial_target)) as target_connection:
                source_connection.backup(target_connection)
        verify_database(partial_target)
        os.replace(partial_target, target_path)
    except Exception:
        partial_target.unlink(missing_ok=True)
        raise

    restored = verify_database(target_path)
    return {
        "operation": "restore",
        "backup": str(backup_path),
        "backup_sha256": expected_sha256,
        "target": str(target_path),
        "restored": restored,
        "pre_restore_backup": pre_restore,
        "restored_at": _utc_now(),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Back up, verify, or restore an llmebm SQLite database")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--source", required=True)
    backup_parser.add_argument("--output", required=True)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--database", required=True)

    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--backup", required=True)
    restore_parser.add_argument("--target", required=True)
    restore_parser.add_argument("--expected-sha256", required=True)
    restore_parser.add_argument("--replace", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.command == "backup":
        receipt = backup_database(args.source, args.output)
    elif args.command == "verify":
        receipt = verify_database(args.database)
    else:
        receipt = restore_database(
            args.backup,
            args.target,
            expected_sha256=args.expected_sha256,
            replace=args.replace,
        )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
