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
                worker_id      TEXT,
                extracted_text TEXT,
                error_log      TEXT,
                metadata_json  TEXT,
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

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS fts_index USING fts5(
                file_hash,
                file_path,
                content
            );

            INSERT OR IGNORE INTO settings (key, value) VALUES ('paused', '0');
        """)
        conn.commit()


def insert_task(db_path, file_hash, file_path, file_type):
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tasks (file_hash, file_path, file_type) VALUES (?, ?, ?)",
            (file_hash, file_path, file_type)
        )
        conn.commit()


def get_task(db_path, file_hash):
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM tasks WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        return dict(row) if row else None


def list_tasks(db_path, status=None, file_type=None, limit=500, offset=0):
    with _connect(db_path) as conn:
        where, params = [], []
        if status:
            where.append("status = ?"); params.append(status)
        if file_type:
            where.append("file_type = ?"); params.append(file_type)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = conn.execute(
            f"SELECT * FROM tasks {clause} ORDER BY last_update DESC LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
        return [dict(r) for r in rows]


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
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT file_hash, file_path, file_type FROM tasks
               WHERE status = 'PENDING' ORDER BY last_update LIMIT 1"""
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


def fts_search(db_path, query, limit=20):
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT file_hash, file_path,
               snippet(fts_index, 2, '<b>', '</b>', '...', 32) AS snippet,
               rank
               FROM fts_index WHERE content MATCH ?
               ORDER BY rank LIMIT ?""",
            (query, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def get_pause_state(db_path):
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'paused'"
        ).fetchone()
        return row is not None and row['value'] == '1'


def set_pause_state(db_path, paused: bool):
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('paused', ?)",
            ('1' if paused else '0',)
        )
        conn.commit()


def hash_exists(db_path, file_hash):
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM tasks WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        return row is not None
