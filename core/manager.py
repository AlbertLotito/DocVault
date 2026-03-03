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
                throttle_state TEXT
            );
        """)
        conn.commit()


def init_settings_db():
    """Create settings.db with just the settings table and paused default."""
    db_path = get_settings_db_path()
    with _connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            INSERT OR IGNORE INTO settings (key, value) VALUES ('paused', '0');

            CREATE TABLE IF NOT EXISTS vault_settings (
                vault_id TEXT NOT NULL,
                key      TEXT NOT NULL,
                value    TEXT NOT NULL,
                PRIMARY KEY (vault_id, key)
            );
        """)
        conn.commit()


@contextmanager
def _connect(db_path):
    conn = sqlite3.connect(db_path, timeout=10)
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
                extracted_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS fts_index USING fts5(
                file_hash,
                file_path,
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
        conn.commit()
        print(f"[bootstrap] Created default vault 'Documents' ({vault_id}) -> {scan_directory}")


def insert_task(db_path, file_hash, file_path, file_type, priority=10,
                file_size=None, file_created=None, file_modified=None,
                vault_id=None):
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO tasks
               (file_hash, file_path, file_type, priority, file_size,
                file_created, file_modified, vault_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (file_hash, file_path, file_type, priority, file_size,
             file_created, file_modified, vault_id)
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
        allowed_sort_by = ['file_path', 'file_type', 'status', 'last_update', 'priority']
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
               last_update = CURRENT_TIMESTAMP WHERE file_hash = ?""",
            (status, worker_id, file_hash)
        )
        conn.commit()


def complete_extraction(db_path, file_hash, status, text=None,
                        metadata=None, error=None):
    with _connect(db_path) as conn:
        conn.execute(
            """UPDATE tasks SET status = ?, extracted_text = ?,
               metadata_json = ?, error_log = ?,
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
        # Update FTS index when text is available
        if text:
            row = conn.execute(
                "SELECT file_path FROM tasks WHERE file_hash = ?", (file_hash,)
            ).fetchone()
            if row:
                conn.execute(
                    "DELETE FROM fts_index WHERE file_hash = ?", (file_hash,)
                )
                conn.execute(
                    "INSERT INTO fts_index (file_hash, file_path, content) VALUES (?, ?, ?)",
                    (file_hash, row['file_path'], text)
                )
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
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.execute(
            """INSERT OR IGNORE INTO extracted_images
               (source_hash, file_path, page_num, image_index, width, height)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                source_hash,
                img_meta['file_path'],
                img_meta.get('page_num'),
                img_meta.get('image_index'),
                img_meta.get('width'),
                img_meta.get('height'),
            )
        )
        conn.commit()
        return True
    except sqlite3.Error as e:
        print(f"DB error in insert_extracted_image: {e}")
        return False
    finally:
        if conn:
            conn.close()


def fts_search(db_path, query, limit=20,
               file_type=None, date_from=None, date_to=None):
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
        clause = " AND ".join(where)
        rows = conn.execute(
            f"""SELECT fts_index.file_hash, fts_index.file_path,
               snippet(fts_index, 2, '<b>', '</b>', '...', 32) AS snippet,
               fts_index.rank
               FROM fts_index
               JOIN tasks ON fts_index.file_hash = tasks.file_hash
               WHERE {clause}
               ORDER BY fts_index.rank LIMIT ?""",
            params + [limit]
        ).fetchall()
        return [dict(r) for r in rows]


def filename_search(db_path, query, limit=50, file_type=None, date_from=None, date_to=None):
    """Search for files by name/path using multi-token substring matching."""
    tokens = [t.strip() for t in query.split() if t.strip()]
    if not tokens:
        return []
    with _connect(db_path) as conn:
        where = []
        params = []
        for token in tokens:
            where.append("LOWER(file_path) LIKE LOWER(?)")
            params.append(f'%{token}%')
        if file_type:
            where.append("LOWER(file_type) = LOWER(?)")
            params.append(file_type.strip().lower())
        if date_from:
            where.append("DATE(COALESCE(file_modified, file_created)) >= ?")
            params.append(date_from)
        if date_to:
            where.append("DATE(COALESCE(file_modified, file_created)) <= ?")
            params.append(date_to)
        params.append(limit)
        sql = f"""
            SELECT file_hash, file_path, file_type, file_size, file_created, file_modified, status
            FROM tasks
            WHERE {' AND '.join(where)}
            ORDER BY file_path COLLATE NOCASE
            LIMIT ?
        """
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_filtered_hashes(db_path, file_type=None, date_from=None, date_to=None):
    """Return list of file_hashes matching constraints, or None if no constraints active."""
    if not any([file_type, date_from, date_to]):
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
            "WHERE status IN ('EXTRACTING', 'EMBEDDING')"
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


def hash_exists(db_path, file_hash):
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM tasks WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        return row is not None
