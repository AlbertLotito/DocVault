#!/usr/bin/env python3
"""
check_db_migration.py — Validates Layer 1 DB schema migration for DocVault.

Verifies that after a server startup:
  - docvault.db has a 'vaults' table with correct columns
  - docvault.db tasks table has a 'vault_id' column
  - At least one vault exists (the 'Documents' default)
  - All tasks have a non-null vault_id (or there are no tasks)
  - settings.db has a 'vault_settings' table
  - logs.db exists with all five required tables

Exit codes:
  0 — all checks passed
  1 — one or more checks failed

Usage:
  python tests/rigs/check_db_migration.py
  python tests/rigs/check_db_migration.py --verbose
  python tests/rigs/check_db_migration.py --db path/to/docvault.db
  python tests/rigs/check_db_migration.py --db /path/to/docvault.db --settings-db /path/to/settings.db --logs-db /path/to/logs.db
"""

import argparse
import sqlite3
import sys
import os

GREEN  = '\033[92m'
RED    = '\033[91m'
YELLOW = '\033[93m'
RESET  = '\033[0m'


def _pass(msg):
    print(f"{GREEN}PASS{RESET}  {msg}")
    return True


def _fail(msg):
    print(f"{RED}FAIL{RESET}  {msg}")
    return False


def _warn(msg):
    print(f"{YELLOW}WARN{RESET}  {msg}")


def check(label, condition, verbose=False, detail=''):
    if condition:
        _pass(label)
        if verbose and detail:
            print(f"       {detail}")
        return True
    return _fail(f"{label}  {detail}")


def run_checks(db_path, settings_db_path, logs_db_path, verbose):
    results = []

    # --- docvault.db ---
    if not os.path.exists(db_path):
        results.append(_fail(f"docvault.db not found at {db_path}"))
        return results

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    results.append(check("vaults table exists", 'vaults' in tables, verbose))

    if 'vaults' in tables:
        vault_cols = {r[1] for r in conn.execute("PRAGMA table_info(vaults)").fetchall()}
        for col in ('vault_id', 'name', 'scan_directory', 'priority', 'color', 'state'):
            results.append(check(f"  vaults.{col} column", col in vault_cols, verbose))

        vault_count = conn.execute("SELECT COUNT(*) FROM vaults").fetchone()[0]
        results.append(check(
            f"At least one vault exists ({vault_count} found)",
            vault_count > 0, verbose
        ))
        if vault_count > 0:
            names = [r[0] for r in conn.execute("SELECT name FROM vaults").fetchall()]
            results.append(check(
                "'Documents' vault exists",
                'Documents' in names, verbose,
                detail=f"Found: {names}"
            ))

    if 'tasks' in tables:
        task_cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        results.append(check("tasks.vault_id column exists", 'vault_id' in task_cols, verbose))

        if 'vault_id' in task_cols:
            total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            if total == 0:
                _warn("No tasks in DB — vault_id assignment check skipped")
            else:
                unvaulted = conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE vault_id IS NULL"
                ).fetchone()[0]
                results.append(check(
                    f"All tasks have vault_id (unvaulted: {unvaulted} of {total})",
                    unvaulted == 0, verbose
                ))
    conn.close()

    # --- settings.db ---
    if not os.path.exists(settings_db_path):
        results.append(_fail(f"settings.db not found at {settings_db_path}"))
    else:
        conn2 = sqlite3.connect(settings_db_path)
        stables = {r[0] for r in conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        results.append(check("vault_settings table in settings.db", 'vault_settings' in stables, verbose))
        conn2.close()

    # --- logs.db ---
    if not os.path.exists(logs_db_path):
        results.append(_fail(f"logs.db not found at {logs_db_path}"))
    else:
        conn3 = sqlite3.connect(logs_db_path)
        ltables = {r[0] for r in conn3.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        for t in ('task_timings', 'worker_errors', 'worker_log', 'extractor_stats', 'system_stats'):
            results.append(check(f"logs.db table '{t}'", t in ltables, verbose))
        conn3.close()

    return results


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--db',          default='docvault.db',
                        help='Path to docvault.db (default: docvault.db)')
    parser.add_argument('--settings-db', default='settings.db',
                        help='Path to settings.db (default: settings.db)')
    parser.add_argument('--logs-db',     default='logs.db',
                        help='Path to logs.db (default: logs.db)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Show detail for each check')
    args = parser.parse_args()

    print(f"\nDocVault Layer 1 — DB Migration Check")
    print(f"  docvault.db : {args.db}")
    print(f"  settings.db : {args.settings_db}")
    print(f"  logs.db     : {args.logs_db}\n")

    results = run_checks(args.db, args.settings_db, args.logs_db, args.verbose)

    true_results = [r for r in results if r is True or r is False]
    passed = sum(1 for r in true_results if r is True)
    failed = sum(1 for r in true_results if r is False)

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
