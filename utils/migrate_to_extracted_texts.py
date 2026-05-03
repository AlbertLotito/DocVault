"""
One-time migration: move tasks.extracted_text -> extracted_texts table.

Run with server stopped:
    .\\venv\\Scripts\\python.exe utils/migrate_to_extracted_texts.py

Safe to re-run -- all steps are idempotent.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.manager import get_db_path, _connect, init_db


def migrate():
    db_path = get_db_path()
    print(f"Migration target: {db_path}")

    # Step 1 — ensure extracted_texts table exists (init_db is idempotent)
    print("Step 1: Ensuring schema is up to date...")
    init_db(db_path)

    with _connect(db_path) as conn:
        # Step 2 — count source rows (column may already be dropped on re-run)
        cols = [r['name'] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
        already = conn.execute("SELECT COUNT(*) FROM extracted_texts").fetchone()[0]
        if 'extracted_text' not in cols:
            print(f"Step 2: tasks.extracted_text already dropped, {already} rows in extracted_texts. Nothing to copy.")
            total = 0
        else:
            total = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE extracted_text IS NOT NULL AND extracted_text != ''"
            ).fetchone()[0]
            print(f"Step 2: {total} rows to migrate, {already} already in extracted_texts.")

        # Step 3 — copy in batches (skip if column already dropped)
        copied = 0
        if total > 0:
            print("Step 3: Copying extracted_text -> extracted_texts...")
            batch_size = 1000
            offset = 0
            t0 = time.time()
            while True:
                rows = conn.execute(
                    """SELECT file_hash, extracted_text FROM tasks
                       WHERE extracted_text IS NOT NULL AND extracted_text != ''
                       LIMIT ? OFFSET ?""",
                    (batch_size, offset)
                ).fetchall()
                if not rows:
                    break
                conn.executemany(
                    """INSERT OR IGNORE INTO extracted_texts (file_hash, extracted_text, stored_at)
                       VALUES (?, ?, CURRENT_TIMESTAMP)""",
                    [(r['file_hash'], r['extracted_text']) for r in rows]
                )
                conn.commit()
                copied += len(rows)
                offset += batch_size
                elapsed = time.time() - t0
                rate = copied / elapsed if elapsed > 0 else 0
                print(f"  {copied}/{total} copied ({rate:.0f} rows/s)...")
            print(f"Step 3 done: {copied} rows copied in {time.time() - t0:.1f}s.")
        else:
            print("Step 3: Skipped (nothing to copy).")

        # Step 4 — verify counts match
        migrated = conn.execute("SELECT COUNT(*) FROM extracted_texts").fetchone()[0]
        print(f"Step 4: Verification -- {migrated} rows in extracted_texts.")
        if total > 0 and migrated < total * 0.99:
            print(f"ERROR: Expected ~{total}, got {migrated}. Aborting DROP COLUMN.")
            sys.exit(1)

        # Step 5 — drop the column from tasks
        try:
            print("Step 5: Dropping tasks.extracted_text column...")
            conn.execute("ALTER TABLE tasks DROP COLUMN extracted_text")
            conn.commit()
            print("Step 5 done.")
        except Exception as e:
            if 'no such column' in str(e).lower():
                print("Step 5: Column already dropped -- skipping.")
            else:
                raise

    # Step 6 — VACUUM (outside transaction -- cannot run inside one)
    print("Step 6: Running VACUUM to reclaim disk space (this may take several minutes)...")
    import sqlite3
    raw = sqlite3.connect(db_path)
    raw.execute("VACUUM")
    raw.close()
    print("Step 6 done.")
    print("\nMigration complete.")


if __name__ == "__main__":
    migrate()
