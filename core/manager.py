import re
import sqlite3
import json
import os
from contextlib import contextmanager


def get_db_path(db_path=None):
    if db_path:
        return db_path
    import configparser
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(os.path.dirname(__file__), '..', 'config.ini'))
    return cfg['database']['sqlite_path']


def get_settings_db_path():
    """Returns the path to settings.db, alongside the main database."""
    main_db = get_db_path()
    return os.path.join(os.path.dirname(main_db), 'settings.db')


def get_logs_db_path():
    """Returns the path to logs.db, alongside the main database."""
    main_db = get_db_path()
    return os.path.join(os.path.dirname(main_db), 'logs.db')


def init_logs_db():
    """Create logs.db with observability tables. Idempotent — safe to call on every startup."""
    db_path = get_logs_db_path()
    with _connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS task_timings (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                file_hash     TEXT,
                vault_id      TEXT,
                extractor     TEXT,
                file_size     INTEGER,
                page_count    INTEGER,
                duration_secs REAL,
                elapsed_secs  REAL,
                completed_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS worker_errors (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                file_hash     TEXT,
                vault_id      TEXT,
                extractor     TEXT,
                error_type    TEXT,
                error_message TEXT,
                traceback     TEXT,
                occurred_at   TEXT
            );

            CREATE TABLE IF NOT EXISTS worker_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                file_hash     TEXT,
                vault_id      TEXT,
                extractor     TEXT,
                level         TEXT,
                message       TEXT,
                occurred_at   TEXT
            );

            CREATE TABLE IF NOT EXISTS extractor_stats (
                vault_id      TEXT,
                extractor     TEXT,
                sample_count  INTEGER DEFAULT 0,
                avg_secs      REAL DEFAULT 0,
                p50_secs      REAL DEFAULT 0,
                p95_secs      REAL DEFAULT 0,
                last_updated  TEXT,
                PRIMARY KEY (vault_id, extractor)
            );

            CREATE TABLE IF NOT EXISTS system_stats (
                sampled_at    TEXT PRIMARY KEY,
                cpu_pct       REAL,
                cpu_temp      REAL,
                ram_used_gb   REAL,
                ram_total_gb  REAL,
                ram_pct       REAL,
                gpu_temp      REAL,
                gpu_util_pct  REAL,
                vram_used_gb  REAL,
                vram_total_gb REAL,
                disk_free_gb  REAL,
                disk_free_pct REAL,
                throttle_state TEXT
            );

            CREATE TABLE IF NOT EXISTS benchmark_runs (
                run_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at                 TEXT NOT NULL,
                results                TEXT NOT NULL,
                bottleneck_extractor   TEXT,
                overall_files_per_hour REAL
            );

            CREATE TABLE IF NOT EXISTS optimizer_profiles (
                profile_id  INTEGER PRIMARY KEY AUTOINCREMENT,
                saved_at    TEXT NOT NULL,
                name        TEXT NOT NULL,
                params      TEXT NOT NULL,
                throughput  REAL,
                source_run  TEXT
            );
        """)

        # Logs DB migrations
        cursor = conn.execute("PRAGMA table_info(system_stats)")
        columns = [row['name'] for row in cursor.fetchall()]
        if 'disk_free_gb' not in columns:
            conn.execute("ALTER TABLE system_stats ADD COLUMN disk_free_gb REAL")
        if 'disk_free_pct' not in columns:
            conn.execute("ALTER TABLE system_stats ADD COLUMN disk_free_pct REAL")
        if 'extracted_queue' not in columns:
            conn.execute("ALTER TABLE system_stats ADD COLUMN extracted_queue INTEGER")
        if 'embedding_queue' not in columns:
            conn.execute("ALTER TABLE system_stats ADD COLUMN embedding_queue INTEGER")

        conn.commit()


def init_settings_db():
    """Create settings.db with the settings and extractor registry tables."""
    db_path = get_settings_db_path()
    with _connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            INSERT OR IGNORE INTO settings (key, value) VALUES ('paused', '0');
            INSERT OR IGNORE INTO settings (key, value) VALUES ('monitor:stuck_task_threshold_mins', '10');

            CREATE TABLE IF NOT EXISTS vault_settings (
                vault_id TEXT NOT NULL,
                key      TEXT NOT NULL,
                value    TEXT NOT NULL,
                PRIMARY KEY (vault_id, key)
            );

            CREATE TABLE IF NOT EXISTS ext_registry (
                kernel_id      TEXT PRIMARY KEY,
                module_name    TEXT NOT NULL,
                version        TEXT NOT NULL,
                file_hash      TEXT NOT NULL,
                is_enabled     INTEGER DEFAULT 0,
                status         TEXT DEFAULT 'unverified',
                extensions     TEXT, -- JSON list
                certified_at   TEXT,
                last_seen_at   TEXT DEFAULT CURRENT_TIMESTAMP,
                kernel_type    TEXT DEFAULT 'python',
                launch_config  TEXT, -- JSON object for subprocess args
                target_type    TEXT DEFAULT 'file', -- 'file' or 'folder'
                description    TEXT
            );
        """)

        # Migrations for ext_registry
        cursor = conn.execute("PRAGMA table_info(ext_registry)")
        ext_columns = [row['name'] for row in cursor.fetchall()]
        if 'kernel_type' not in ext_columns:
            conn.execute("ALTER TABLE ext_registry ADD COLUMN kernel_type TEXT DEFAULT 'python'")
        if 'launch_config' not in ext_columns:
            conn.execute("ALTER TABLE ext_registry ADD COLUMN launch_config TEXT")
        if 'target_type' not in ext_columns:
            conn.execute("ALTER TABLE ext_registry ADD COLUMN target_type TEXT DEFAULT 'file'")
        if 'description' not in ext_columns:
            conn.execute("ALTER TABLE ext_registry ADD COLUMN description TEXT")

        conn.commit()


@contextmanager
def _connect(db_path):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path=None):
    db_path = get_db_path(db_path)
    with _connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                file_hash      TEXT PRIMARY KEY,
                file_path      TEXT NOT NULL,
                file_type      TEXT,
                status         TEXT DEFAULT 'PENDING',
                priority       INTEGER DEFAULT 10,
                worker_id      TEXT,
                extracted_text TEXT,
                error_log      TEXT,
                metadata_json  TEXT,
                file_size      INTEGER,
                file_created   TEXT,
                file_modified  TEXT,
                progress_text  TEXT,
                progress_pct   REAL DEFAULT 0,
                last_update    DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS extracted_images (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                source_hash  TEXT NOT NULL,
                file_path    TEXT NOT NULL UNIQUE,
                page_num     INTEGER,
                image_index  INTEGER,
                width        INTEGER,
                height       INTEGER,
                description  TEXT,
                extracted_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS fts_index USING fts5(
                file_hash UNINDEXED,
                chunk_index UNINDEXED,
                file_path UNINDEXED,
                content
            );

            CREATE TABLE IF NOT EXISTS vaults (
                vault_id       TEXT PRIMARY KEY,
                name           TEXT NOT NULL,
                scan_directory TEXT NOT NULL,
                priority       INTEGER DEFAULT 5,
                color          TEXT DEFAULT '#6366f1',
                state          TEXT DEFAULT 'active',
                created_at     TEXT,
                updated_at     TEXT
            );

            CREATE TABLE IF NOT EXISTS face_registry (
                cluster_id     TEXT PRIMARY KEY, -- UUID
                person_name    TEXT DEFAULT 'Unknown Person',
                thumbnail_path TEXT,
                created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS face_detections (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                file_hash      TEXT NOT NULL,
                cluster_id     TEXT REFERENCES face_registry(cluster_id),
                bounding_box   TEXT, -- JSON: [x, y, w, h]
                encoding_json  TEXT, -- JSON: [128 floats]
                detected_at    DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS file_vault (
                file_hash TEXT NOT NULL REFERENCES tasks(file_hash),
                vault_id  TEXT NOT NULL REFERENCES vaults(vault_id),
                file_path TEXT NOT NULL,
                added_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (file_hash, vault_id)
            );
            CREATE INDEX IF NOT EXISTS idx_file_vault_vault_id ON file_vault(vault_id);
        """)

        # Schema migrations
        cursor = conn.execute("PRAGMA table_info(tasks)")
        columns = [row['name'] for row in cursor.fetchall()]
        if 'priority' not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN priority INTEGER DEFAULT 10")
        if 'file_size' not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN file_size INTEGER")
        if 'file_created' not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN file_created TEXT")
        if 'file_modified' not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN file_modified TEXT")
        if 'vault_id' not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN vault_id TEXT REFERENCES vaults(vault_id)")
        if 'progress_text' not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN progress_text TEXT")
        if 'progress_pct' not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN progress_pct REAL DEFAULT 0")
        if 'parent_hash' not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN parent_hash TEXT REFERENCES tasks(file_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_parent_hash ON tasks(parent_hash)")

        # extracted_images migration
        cursor = conn.execute("PRAGMA table_info(extracted_images)")
        img_columns = [row['name'] for row in cursor.fetchall()]
        if 'description' not in img_columns:
            conn.execute("ALTER TABLE extracted_images ADD COLUMN description TEXT")

        # file_vault backfill — idempotent (INSERT OR IGNORE on PK)
        conn.execute("""
            INSERT OR IGNORE INTO file_vault (file_hash, vault_id, file_path)
            SELECT file_hash, vault_id, file_path FROM tasks WHERE vault_id IS NOT NULL
        """)

        conn.commit()


def bootstrap_default_vault(db_path=None, scan_directory=None):
    """
    If no vaults exist, create the 'Documents' default vault and assign
    all un-vaulted tasks to it. Idempotent — safe to call on every startup.
    """
    import uuid
    from datetime import datetime, timezone

    db_path = get_db_path(db_path)
    with _connect(db_path) as conn:
        existing = conn.execute("SELECT COUNT(*) FROM vaults").fetchone()[0]
        if existing > 0:
            return  # already bootstrapped

        if not scan_directory:
            try:
                scan_directory = get_setting('paths:scan_directory') or '.'
            except Exception:
                scan_directory = '.'

        vault_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
               VALUES (?, 'Documents', ?, 5, 'active', ?, ?)""",
            (vault_id, scan_directory, now, now)
        )
        conn.execute(
            "UPDATE tasks SET vault_id = ? WHERE vault_id IS NULL",
            (vault_id,)
        )
        conn.execute("""
            INSERT OR IGNORE INTO file_vault (file_hash, vault_id, file_path)
            SELECT file_hash, vault_id, file_path FROM tasks WHERE vault_id = ?
        """, (vault_id,))
        conn.commit()
        print(f"[bootstrap] Created default vault 'Documents' ({vault_id}) -> {scan_directory}")


def insert_task(db_path, file_hash, file_path, file_type, priority=10,
                file_size=None, file_created=None, file_modified=None,
                vault_id=None, parent_hash=None, metadata_json=None):
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO tasks
               (file_hash, file_path, file_type, priority, file_size,
                file_created, file_modified, vault_id, parent_hash, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (file_hash, file_path, file_type, priority, file_size,
             file_created, file_modified, vault_id, parent_hash,
             json.dumps(metadata_json) if metadata_json else None)
        )
        conn.commit()


def insert_child_tasks(db_path, child_task_dicts: list, default_vault_id=None):
    """Insert a batch of child tasks in a single transaction. Uses INSERT OR IGNORE."""
    with _connect(db_path) as conn:
        for ct in child_task_dicts:
            conn.execute(
                """INSERT OR IGNORE INTO tasks
                   (file_hash, file_path, file_type, priority,
                    vault_id, parent_hash, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    ct['file_hash'],
                    ct['file_path'],
                    ct['file_type'],
                    ct.get('priority', 5),
                    ct.get('vault_id') or default_vault_id,
                    ct.get('parent_hash'),
                    json.dumps(ct['metadata_json']) if ct.get('metadata_json') else None,
                )
            )
        conn.commit()


def upsert_file_vault(db_path, file_hash, vault_id, file_path):
    """Register (or update) a file-vault membership with the vault-specific path.

    Uses ON CONFLICT DO UPDATE so added_at is preserved on re-scan.
    file_path is normalised before storage so ingestor comparisons are stable.
    """
    if not vault_id:
        return
    norm = os.path.normpath(file_path)
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO file_vault (file_hash, vault_id, file_path)
               VALUES (?, ?, ?)
               ON CONFLICT(file_hash, vault_id) DO UPDATE SET file_path = excluded.file_path""",
            (file_hash, vault_id, norm)
        )
        conn.commit()


def get_file_vault_path(db_path, file_hash, vault_id):
    """Return the stored file_path for (file_hash, vault_id), or None if not registered."""
    if not vault_id:
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT file_path FROM file_vault WHERE file_hash = ? AND vault_id = ?",
            (file_hash, vault_id),
        ).fetchone()
    return row['file_path'] if row else None


def get_vault_paths(db_path, file_hashes, vault_id):
    """Return {file_hash: file_path} from file_vault for the given vault and hashes.

    Returns {} on any DB error so callers silently keep canonical paths.
    Uses parameterised IN clause — never string-interpolated input.
    """
    if not file_hashes:
        return {}
    try:
        file_hashes = list(file_hashes)
        placeholders = ','.join(['?'] * len(file_hashes))
        with _connect(db_path) as conn:
            rows = conn.execute(
                f"SELECT file_hash, file_path FROM file_vault"
                f" WHERE vault_id = ? AND file_hash IN ({placeholders})",
                [vault_id, *file_hashes],
            ).fetchall()
        return {r['file_hash']: r['file_path'] for r in rows}
    except Exception:
        return {}


def get_task_metadata(db_path, file_hash) -> dict:
    """Return the metadata_json dict for a task, or {} if absent."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT metadata_json FROM tasks WHERE file_hash = ?", (file_hash,)
        ).fetchone()
    if not row or not row['metadata_json']:
        return {}
    try:
        return json.loads(row['metadata_json'])
    except Exception:
        return {}


def update_task_metadata(db_path, file_hash, metadata: dict):
    """Merge metadata dict into the task's existing metadata_json."""
    existing = get_task_metadata(db_path, file_hash)
    existing.update(metadata)
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE tasks SET metadata_json = ? WHERE file_hash = ?",
            (json.dumps(existing), file_hash)
        )
        conn.commit()


def update_fts(db_path, file_hash):
    """Re-sync FTS index for a single task from its current extracted_text."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT file_path, extracted_text FROM tasks WHERE file_hash = ?",
            (file_hash,)
        ).fetchone()
        if not row:
            return
        conn.execute("DELETE FROM fts_index WHERE file_hash = ?", (file_hash,))
        if row['extracted_text']:
            try:
                from embeddings.chunker import chunk
                chunks = chunk(row['extracted_text'])
                for i, chunk_text in enumerate(chunks):
                    conn.execute(
                        "INSERT INTO fts_index (file_hash, chunk_index, file_path, content) VALUES (?, ?, ?, ?)",
                        (file_hash, i, row['file_path'], chunk_text)
                    )
            except Exception as e:
                print(f"[manager] Warning: FTS update failed for {file_hash}: {e}")
        conn.commit()


def append_parent_text(db_path, parent_hash, suffix: str):
    """Append suffix to parent task's extracted_text and update FTS. Idempotent."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT extracted_text FROM tasks WHERE file_hash = ?", (parent_hash,)
        ).fetchone()
        if not row:
            return
        existing = row['extracted_text'] or ''
        # Idempotency: the suffix starts with "[Image, Page N, #M (hash8):" —
        # extract the guard token (everything up to and including the closing paren)
        guard = suffix.split('):')[0] + '):' if '):' in suffix else suffix[:30]
        if guard in existing:
            return
        conn.execute(
            "UPDATE tasks SET extracted_text = ? WHERE file_hash = ?",
            (existing + '\n\n' + suffix, parent_hash)
        )
        conn.commit()
    update_fts(db_path, parent_hash)


def reset_to_extracted_if_complete(db_path, file_hash):
    """Atomically reset a COMPLETED task to EXTRACTED for re-embedding."""
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE tasks SET status = 'EXTRACTED' WHERE file_hash = ? AND status = 'COMPLETED'",
            (file_hash,)
        )
        conn.commit()


def update_task_path(db_path, file_hash, new_path):
    """Update the file path for a task (e.g. after a file has been moved/renamed)."""
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE tasks SET file_path = ?, last_update = CURRENT_TIMESTAMP WHERE file_hash = ?",
            (new_path, file_hash)
        )
        conn.commit()


def get_task(db_path, file_hash):
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM tasks WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        return dict(row) if row else None


def list_tasks(db_path, status=None, file_type=None, vault_id=None, limit=50, offset=0, sort_by='last_update', sort_order='DESC'):
    with _connect(db_path) as conn:
        # Prevent SQL injection by validating sort parameters
        allowed_sort_by = ['file_path', 'file_type', 'status', 'last_update', 'priority', 'file_size', 'vault_id']
        if sort_by not in allowed_sort_by:
            sort_by = 'last_update'

        if sort_order.upper() not in ['ASC', 'DESC']:
            sort_order = 'DESC'

        where, params = [], []
        if status:
            where.append("status = ?"); params.append(status)
        if file_type and file_type.strip():
            where.append("file_type LIKE ?"); params.append(f"%{file_type.strip()}%")
        if vault_id:
            where.append("vault_id = ?"); params.append(vault_id)

        clause = f"WHERE {' AND '.join(where)}" if where else ""

        # Get total count for pagination
        count_query = f"SELECT COUNT(*) FROM tasks {clause}"
        total_matches = conn.execute(count_query, params).fetchone()[0]

        # Get paginated results
        query = f"SELECT * FROM tasks {clause} ORDER BY {sort_by} {sort_order} LIMIT ? OFFSET ?"
        paginated_params = params + [limit, offset]
        
        rows = conn.execute(query, paginated_params).fetchall()
        
        return {
            "tasks": [dict(r) for r in rows],
            "total_matches": total_matches,
        }


def update_task_status(db_path, file_hash, status, worker_id=None):
    with _connect(db_path) as conn:
        conn.execute(
            """UPDATE tasks SET status = ?, worker_id = ?,
               progress_text = NULL, progress_pct = 0,
               last_update = CURRENT_TIMESTAMP WHERE file_hash = ?""",
            (status, worker_id, file_hash)
        )
        conn.commit()


def update_task_progress(db_path, file_hash, text, pct):
    with _connect(db_path) as conn:
        conn.execute(
            """UPDATE tasks SET progress_text = ?, progress_pct = ?,
               last_update = CURRENT_TIMESTAMP WHERE file_hash = ?""",
            (text, pct, file_hash)
        )
        conn.commit()


def get_active_tasks(db_path):
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT t.*, v.name as vault_name, v.color as vault_color
               FROM tasks t
               LEFT JOIN vaults v ON t.vault_id = v.vault_id
               WHERE t.status IN ('PROCESSING', 'EMBEDDING')
               ORDER BY t.last_update DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_system_stats_history(hours=1):
    db_path = get_logs_db_path()
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT * FROM system_stats
               WHERE sampled_at >= datetime('now', ?)
               ORDER BY sampled_at ASC""",
            (f'-{hours} hours',)
        ).fetchall()
        return [dict(r) for r in rows]


def complete_extraction(db_path, file_hash, status, text=None,
                        metadata=None, error=None):
    with _connect(db_path) as conn:
        conn.execute(
            """UPDATE tasks SET status = ?, extracted_text = ?,
               metadata_json = ?, error_log = ?,
               progress_text = NULL, progress_pct = 100,
               last_update = CURRENT_TIMESTAMP
               WHERE file_hash = ?""",
            (
                status,
                text,
                json.dumps(metadata) if metadata else None,
                error,
                file_hash,
            )
        )
        # Update FTS index when text is available (index chunks for better RAG)
        if text:
            row = conn.execute(
                "SELECT file_path FROM tasks WHERE file_hash = ?", (file_hash,)
            ).fetchone()
            if row:
                try:
                    conn.execute(
                        "DELETE FROM fts_index WHERE file_hash = ?", (file_hash,)
                    )
                    from embeddings.chunker import chunk
                    chunks = chunk(text)
                    for i, chunk_text in enumerate(chunks):
                        conn.execute(
                            "INSERT INTO fts_index (file_hash, chunk_index, file_path, content) VALUES (?, ?, ?, ?)",
                            (file_hash, i, row['file_path'], chunk_text)
                        )
                except Exception as e:
                    print(f"[manager] Warning: FTS index update failed: {e}")
        conn.commit()


def claim_pending_task(db_path, worker_id):
    """
    Claim the highest-priority PENDING task using composite priority:
        effective_priority = (10 - vault_priority) * extractor_priority + age_bonus
    where age_bonus = seconds_since_last_update / 3600
    Lower vault_priority = higher importance (priority 1 beats priority 9).
    Larger effective_priority value wins (ORDER BY DESC).
    """
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT t.file_hash, t.file_path, t.file_type, t.priority, t.vault_id,
                      t.parent_hash, t.metadata_json,
                      COALESCE(v.priority, 5) AS vault_priority
               FROM tasks t
               LEFT JOIN vaults v ON t.vault_id = v.vault_id
               WHERE t.status = 'PENDING'
               ORDER BY
                 -- Lower vault_priority = more important -> invert with (10 - vault_priority)
                 ((10 - COALESCE(v.priority, 5)) * COALESCE(t.priority, 10))
                 + (CAST(
                     (julianday('now') - julianday(t.last_update)) * 86400
                    AS REAL) / 3600.0)
                 DESC
               LIMIT 1"""
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        task = dict(row)
        conn.execute(
            """UPDATE tasks SET status = 'PROCESSING', worker_id = ?,
               last_update = CURRENT_TIMESTAMP WHERE file_hash = ?""",
            (worker_id, task['file_hash'])
        )
        conn.commit()
        task['status'] = 'PROCESSING'
        return task


def claim_extracted_task(db_path, worker_id):
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT file_hash, file_path, file_type, extracted_text
               FROM tasks WHERE status = 'EXTRACTED' ORDER BY last_update LIMIT 1"""
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        task = dict(row)
        conn.execute(
            """UPDATE tasks SET status = 'EMBEDDING', worker_id = ?,
               last_update = CURRENT_TIMESTAMP WHERE file_hash = ?""",
            (worker_id, task['file_hash'])
        )
        conn.commit()
        return task


def claim_extracted_tasks(db_path, worker_id, limit=8):
    """Claim up to *limit* EXTRACTED tasks in one transaction.

    Returns a list of task dicts (same shape as claim_extracted_task).
    Returns [] when the queue is empty.
    """
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """SELECT file_hash, file_path, file_type, extracted_text
               FROM tasks WHERE status = 'EXTRACTED' ORDER BY last_update LIMIT ?""",
            (limit,)
        ).fetchall()
        if not rows:
            conn.commit()
            return []
        tasks = [dict(r) for r in rows]
        hashes = [t['file_hash'] for t in tasks]
        conn.execute(
            f"""UPDATE tasks SET status = 'EMBEDDING', worker_id = ?,
               last_update = CURRENT_TIMESTAMP
               WHERE file_hash IN ({','.join('?' * len(hashes))})""",
            [worker_id, *hashes]
        )
        conn.commit()
        return tasks


def get_stats(db_path):
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as n FROM tasks GROUP BY status"
        ).fetchall()
        counts = {r['status']: r['n'] for r in rows}
        total = sum(counts.values())
        return {
            'total': total,
            'pending': counts.get('PENDING', 0),
            'processing': counts.get('PROCESSING', 0),
            'extracted': counts.get('EXTRACTED', 0),
            'embedding': counts.get('EMBEDDING', 0),
            'completed': counts.get('COMPLETED', 0),
            'unknown': counts.get('UNKNOWN', 0),
            'error': counts.get('ERROR', 0),
        }


def insert_extracted_image(db_path, source_hash, img_meta):
    if 'file_path' not in img_meta:
        print("Error: img_meta missing required key 'file_path'")
        return False
    try:
        with _connect(db_path) as conn:
            conn.execute(
                """INSERT OR IGNORE INTO extracted_images
                   (source_hash, file_path, page_num, image_index, width, height, description)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    source_hash,
                    img_meta['file_path'],
                    img_meta.get('page_num'),
                    img_meta.get('image_index'),
                    img_meta.get('width'),
                    img_meta.get('height'),
                    img_meta.get('description'),
                )
            )
            conn.commit()
            return True
    except sqlite3.Error as e:
        print(f"DB error in insert_extracted_image: {e}")
        return False


def fts_search(db_path, query, limit=20,
               file_type=None, date_from=None, date_to=None, vault_ids=None):
    with _connect(db_path) as conn:
        where = ["fts_index.content MATCH ?"]
        params = [query]
        if file_type:
            where.append("tasks.file_type LIKE ?")
            params.append(f"%{file_type.strip()}%")
        if date_from:
            where.append("tasks.file_modified >= ?")
            params.append(date_from)
        if date_to:
            where.append("tasks.file_modified <= ?")
            params.append(date_to + "T23:59:59")
        if vault_ids:
            where.append(f"tasks.vault_id IN ({','.join(['?']*len(vault_ids))})")
            params.extend(vault_ids)
        clause = " AND ".join(where)
        rows = conn.execute(
            f"""SELECT fts_index.file_hash, fts_index.chunk_index, fts_index.file_path,
               fts_index.content AS chunk_text,
               snippet(fts_index, 3, '<b>', '</b>', '...', 32) AS snippet,
               fts_index.rank
               FROM fts_index
               JOIN tasks ON fts_index.file_hash = tasks.file_hash
               WHERE {clause}
               ORDER BY fts_index.rank LIMIT ?""",
            params + [limit]
        ).fetchall()
        return [dict(r) for r in rows]


def _register_regexp(conn):
    """Register a case-insensitive REGEXP function on a sqlite3 connection."""
    def _regexp(pattern, text):
        if text is None:
            return False
        try:
            return bool(re.search(pattern, text, re.IGNORECASE))
        except re.error:
            return False
    conn.create_function('REGEXP', 2, _regexp)


def _wildcard_to_like(pattern: str) -> str:
    """Convert shell-style wildcard (* ?) to SQL LIKE pattern (% _).

    Existing SQL wildcard characters (% and _) in the pattern are escaped
    so they match literally.
    """
    pattern = pattern.replace('%', r'\%').replace('_', r'\_')
    pattern = pattern.replace('*', '%').replace('?', '_')
    return pattern


def _filename_filter_clauses(file_type, date_from, date_to, vault_ids=None):
    """Return (where_fragments, params) for the common filename filter fields."""
    where, params = [], []
    if file_type:
        where.append("LOWER(file_type) = LOWER(?)")
        params.append(file_type.strip().lower())
    if date_from:
        where.append("DATE(COALESCE(file_modified, file_created)) >= ?")
        params.append(date_from)
    if date_to:
        where.append("DATE(COALESCE(file_modified, file_created)) <= ?")
        params.append(date_to)
    if vault_ids:
        where.append(f"vault_id IN ({','.join(['?']*len(vault_ids))})")
        params.extend(vault_ids)
    return where, params


_FILENAME_SELECT = ("SELECT file_hash, file_path, file_type, file_size, "
                    "file_created, file_modified, status FROM tasks")


def filename_search(db_path, query, limit=50, file_type=None, date_from=None, date_to=None, vault_ids=None):
    """Search for files by name/path.

    Query shapes (auto-detected by search.query.detect_mode):
      /pattern/  — regex match on file_path (case-insensitive)
      word*/?    — wildcard: * → SQL %, ? → SQL _ (single LIKE pattern)
      plain text — multi-token substring matching (all tokens must appear)
    """
    from search.query import detect_mode
    mode, value = detect_mode(query)

    if mode == 'regex':
        try:
            re.compile(value)
        except re.error:
            return []
        with _connect(db_path) as conn:
            _register_regexp(conn)
            where = ["file_path REGEXP ?"]
            params = [value]
            extra_where, extra_params = _filename_filter_clauses(file_type, date_from, date_to, vault_ids)
            where += extra_where
            params += extra_params
            params.append(limit)
            rows = conn.execute(
                f"{_FILENAME_SELECT} WHERE {' AND '.join(where)}"
                f" ORDER BY file_path COLLATE NOCASE LIMIT ?", params
            ).fetchall()
        return [dict(r) for r in rows]

    if mode == 'wildcard':
        like_pat = _wildcard_to_like(value)
        # Wrap with % so the pattern matches anywhere in the path (substring),
        # matching the same "contains" behaviour as plain multi-token search.
        if not like_pat.startswith('%'):
            like_pat = '%' + like_pat
        if not like_pat.endswith('%'):
            like_pat = like_pat + '%'
        with _connect(db_path) as conn:
            where = ["file_path LIKE ? ESCAPE '\\'"]
            params = [like_pat]
            extra_where, extra_params = _filename_filter_clauses(file_type, date_from, date_to, vault_ids)
            where += extra_where
            params += extra_params
            params.append(limit)
            rows = conn.execute(
                f"{_FILENAME_SELECT} WHERE {' AND '.join(where)}"
                f" ORDER BY file_path COLLATE NOCASE LIMIT ?", params
            ).fetchall()
        return [dict(r) for r in rows]

    # plain — multi-token LIKE (all tokens must match anywhere in path)
    tokens = [t.strip() for t in query.split() if t.strip()]
    if not tokens:
        return []
    with _connect(db_path) as conn:
        where = [f"LOWER(file_path) LIKE LOWER(?)" for _ in tokens]
        params = [f'%{t}%' for t in tokens]
        extra_where, extra_params = _filename_filter_clauses(file_type, date_from, date_to)
        where += extra_where
        params += extra_params
        params.append(limit)
        rows = conn.execute(
            f"{_FILENAME_SELECT} WHERE {' AND '.join(where)}"
            f" ORDER BY file_path COLLATE NOCASE LIMIT ?", params
        ).fetchall()
        return [dict(r) for r in rows]


def get_filtered_hashes(db_path, file_type=None, date_from=None, date_to=None, vault_ids=None):
    """Return list of file_hashes matching constraints, or None if no constraints active."""
    if not any([file_type, date_from, date_to, vault_ids]):
        return None  # no filter — caller should not restrict Qdrant
    where, params = [], []
    if file_type:
        where.append("file_type LIKE ?")
        params.append(f"%{file_type.strip()}%")
    if date_from:
        where.append("file_modified >= ?")
        params.append(date_from)
    if date_to:
        where.append("file_modified <= ?")
        params.append(date_to + "T23:59:59")
    if vault_ids:
        where.append(f"vault_id IN ({','.join(['?']*len(vault_ids))})")
        params.extend(vault_ids)
    clause = "WHERE " + " AND ".join(where)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT file_hash FROM tasks {clause}", params
        ).fetchall()
    return [r['file_hash'] for r in rows]


def get_pause_state():
    with _connect(get_settings_db_path()) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'paused'"
        ).fetchone()
        return row is not None and row['value'] == '1'


def set_pause_state(paused: bool):
    with _connect(get_settings_db_path()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('paused', ?)",
            ('1' if paused else '0',)
        )
        conn.commit()


def reprocess_task(db_path, file_hash):
    with _connect(db_path) as conn:
        conn.execute(
            """UPDATE tasks SET status='PENDING', worker_id=NULL,
               extracted_text=NULL, error_log=NULL,
               last_update=CURRENT_TIMESTAMP WHERE file_hash=?""",
            (file_hash,)
        )
        conn.commit()


def reset_stuck_tasks(db_path) -> int:
    with _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE tasks SET status='PENDING', worker_id=NULL "
            "WHERE status IN ('PROCESSING', 'EMBEDDING')"
        )
        conn.commit()
        return cur.rowcount


def get_setting(key):
    with _connect(get_settings_db_path()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row['value'] if row else None


def set_setting(key, value):
    with _connect(get_settings_db_path()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value))
        )
        conn.commit()


def get_face_registry(db_path):
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT r.*, COUNT(d.id) as detection_count 
               FROM face_registry r
               LEFT JOIN face_detections d ON r.cluster_id = d.cluster_id
               GROUP BY r.cluster_id
               ORDER BY detection_count DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


def rename_person(db_path, cluster_id, new_name):
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE face_registry SET person_name = ? WHERE cluster_id = ?",
            (new_name, cluster_id)
        )
        conn.commit()


def get_person_detections(db_path, cluster_id, limit=50):
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT d.*, t.file_path 
               FROM face_detections d
               JOIN tasks t ON d.file_hash = t.file_hash
               WHERE d.cluster_id = ?
               ORDER BY d.detected_at DESC
               LIMIT ?""",
            (cluster_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def merge_people(db_path, target_cluster_id, source_cluster_id):
    """Combine two identities into one."""
    with _connect(db_path) as conn:
        # 1. Update all detections from source to target
        conn.execute(
            "UPDATE face_detections SET cluster_id = ? WHERE cluster_id = ?",
            (target_cluster_id, source_cluster_id)
        )
        # 2. Delete the source registry entry
        conn.execute(
            "DELETE FROM face_registry WHERE cluster_id = ?",
            (source_cluster_id,)
        )
        conn.commit()


def hash_exists(db_path, file_hash):
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM tasks WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        return row is not None
