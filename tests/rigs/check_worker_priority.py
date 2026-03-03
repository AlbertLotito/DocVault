#!/usr/bin/env python3
"""
check_worker_priority.py -- Validates composite priority task scheduling.

Inserts tasks with different vault priorities and ages, then checks
that claim_pending_task() returns them in the correct order.

Exit codes: 0 = pass, 1 = fail

Usage:
  python tests/rigs/check_worker_priority.py
  python tests/rigs/check_worker_priority.py --verbose
"""
import argparse, sqlite3, sys, os, tempfile

GREEN = '\033[92m'; RED = '\033[91m'; RESET = '\033[0m'
def _pass(msg): print(f"{GREEN}PASS{RESET}  {msg}"); return True
def _fail(msg): print(f"{RED}FAIL{RESET}  {msg}"); return False

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    print("\nDocVault Layer 2 -- Worker Priority Check\n")
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    results = []

    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, 'test.db')
        sdb = os.path.join(td, 'settings.db')
        import core.manager as m
        orig_db = m.get_db_path
        orig_sdb = m.get_settings_db_path
        m.get_db_path = lambda db_path=None: db
        m.get_settings_db_path = lambda: sdb
        try:
            m.init_settings_db(); m.init_db(db)
            conn = sqlite3.connect(db)
            conn.execute("INSERT INTO vaults VALUES ('vh','High','E:/h',1,'#f','active',NULL,NULL)")
            conn.execute("INSERT INTO vaults VALUES ('vl','Low', 'E:/l',9,'#0','active',NULL,NULL)")
            conn.execute("INSERT INTO tasks (file_hash,file_path,file_type,status,priority,vault_id) "
                         "VALUES ('h1','/h.pdf','pdf','PENDING',10,'vh')")
            conn.execute("INSERT INTO tasks (file_hash,file_path,file_type,status,priority,vault_id) "
                         "VALUES ('l1','/l.pdf','pdf','PENDING',10,'vl')")
            conn.commit(); conn.close()

            task = m.claim_pending_task(db, 'rig')
            results.append(
                _pass(f"High-priority vault task claimed first: {task['file_hash']}") if task and task['file_hash']=='h1'
                else _fail(f"Expected 'h1', got {task}")
            )

            # Aging test: same vault, old task vs new task — old should win
            conn = sqlite3.connect(db)
            conn.execute("INSERT INTO vaults VALUES ('vm','Mid','E:/m',5,'#8','active',NULL,NULL)")
            conn.execute("""INSERT INTO tasks (file_hash,file_path,file_type,status,priority,vault_id,last_update)
                            VALUES ('old1','/old.pdf','pdf','PENDING',5,'vm',
                            datetime('now','-7200 seconds'))""")
            conn.execute("""INSERT INTO tasks (file_hash,file_path,file_type,status,priority,vault_id,last_update)
                            VALUES ('new1','/new.pdf','pdf','PENDING',5,'vm',
                            datetime('now'))""")
            # Mark h1/l1 as DONE so they don't interfere
            conn.execute("UPDATE tasks SET status='DONE' WHERE file_hash IN ('h1','l1')")
            conn.commit(); conn.close()

            task = m.claim_pending_task(db, 'rig')
            results.append(
                _pass(f"Aged task wins after 2h on same vault: {task['file_hash']}") if task and task['file_hash']=='old1'
                else _fail(f"Aging not working: expected 'old1', got {task}")
            )
        finally:
            m.get_db_path = orig_db
            m.get_settings_db_path = orig_sdb

    failed = sum(1 for r in results if not r)
    print(f"\n{len(results)-failed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)

if __name__ == '__main__':
    main()
