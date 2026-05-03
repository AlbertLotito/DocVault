"""Tests for the extracted_texts migration script."""
import sqlite3
import pytest


@pytest.fixture
def legacy_db(tmp_path):
    """A DB with the old schema: extracted_text inline in tasks."""
    db = str(tmp_path / 'docvault.db')
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE tasks (
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
            vault_id       TEXT,
            parent_hash    TEXT,
            progress_text  TEXT,
            progress_pct   REAL DEFAULT 0,
            last_update    DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE VIRTUAL TABLE fts_index USING fts5(
            file_hash UNINDEXED,
            chunk_index UNINDEXED,
            file_path UNINDEXED,
            content
        );
        CREATE TABLE vaults (
            vault_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            scan_directory TEXT NOT NULL,
            priority INTEGER DEFAULT 5,
            color TEXT DEFAULT '#6366f1',
            state TEXT DEFAULT 'active',
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE extracted_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_hash TEXT NOT NULL,
            file_path TEXT NOT NULL UNIQUE
        );
        CREATE TABLE face_detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_hash TEXT NOT NULL,
            cluster_id TEXT
        );
        CREATE TABLE file_vault (
            file_hash TEXT NOT NULL,
            vault_id  TEXT NOT NULL,
            file_path TEXT NOT NULL,
            PRIMARY KEY (file_hash, vault_id)
        );
        CREATE TABLE face_registry (
            cluster_id TEXT PRIMARY KEY,
            person_name TEXT DEFAULT 'Unknown Person'
        );
    """)
    conn.executemany(
        "INSERT INTO tasks (file_hash, file_path, file_type, status, extracted_text) VALUES (?,?,?,?,?)",
        [
            ('hash1', '/docs/a.pdf', 'pdf', 'COMPLETED', 'Text for doc A'),
            ('hash2', '/docs/b.pdf', 'pdf', 'COMPLETED', 'Text for doc B'),
            ('hash3', '/docs/c.pdf', 'pdf', 'PENDING',   None),
        ]
    )
    conn.commit()
    conn.close()
    return db


def test_migration_copies_text(legacy_db, monkeypatch):
    """Migration copies extracted_text from tasks to extracted_texts."""
    import utils.migrate_to_extracted_texts as m
    from core import manager
    monkeypatch.setattr(m, 'get_db_path', lambda: legacy_db)
    monkeypatch.setattr(m, 'init_db', lambda db: manager.init_db(db))
    m.migrate()

    conn = sqlite3.connect(legacy_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT file_hash, extracted_text FROM extracted_texts ORDER BY file_hash"
    ).fetchall()
    conn.close()

    assert len(rows) == 2
    assert rows[0]['file_hash'] == 'hash1'
    assert rows[0]['extracted_text'] == 'Text for doc A'
    assert rows[1]['file_hash'] == 'hash2'
    assert rows[1]['extracted_text'] == 'Text for doc B'


def test_migration_drops_column(legacy_db, monkeypatch):
    """After migration, tasks.extracted_text column is gone."""
    import utils.migrate_to_extracted_texts as m
    from core import manager
    monkeypatch.setattr(m, 'get_db_path', lambda: legacy_db)
    monkeypatch.setattr(m, 'init_db', lambda db: manager.init_db(db))
    m.migrate()

    conn = sqlite3.connect(legacy_db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    conn.close()
    assert 'extracted_text' not in cols


def test_migration_is_idempotent(legacy_db, monkeypatch):
    """Running migration twice does not raise or corrupt data."""
    import utils.migrate_to_extracted_texts as m
    from core import manager
    monkeypatch.setattr(m, 'get_db_path', lambda: legacy_db)
    monkeypatch.setattr(m, 'init_db', lambda db: manager.init_db(db))
    m.migrate()
    m.migrate()  # second run -- must not raise

    conn = sqlite3.connect(legacy_db)
    count = conn.execute("SELECT COUNT(*) FROM extracted_texts").fetchone()[0]
    conn.close()
    assert count == 2
