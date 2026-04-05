import pytest
from datetime import datetime, timedelta
from unittest.mock import patch
from core import manager
from core.manager import _connect


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    return db_path


@pytest.fixture
def priority_db(tmp_path):
    """DB with two vaults: vault-hi (priority 1) and vault-lo (priority 9)."""
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('vault-hi', 'High Priority', '/hi', 1, 'active', '2024-01-01', '2024-01-01')""")
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('vault-lo', 'Low Priority', '/lo', 9, 'active', '2024-01-01', '2024-01-01')""")
        conn.commit()
    return db_path


def _insert_task(conn, file_hash, vault_id, status, last_update=None, file_path=None):
    """Helper: insert a minimal task row with controllable last_update."""
    if last_update is None:
        last_update = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    if file_path is None:
        file_path = f'/{vault_id}/{file_hash}.txt'
    conn.execute(
        """INSERT INTO tasks (file_hash, file_path, file_type, status, vault_id, last_update)
           VALUES (?, ?, 'txt', ?, ?, ?)""",
        (file_hash, file_path, status, vault_id, last_update)
    )


def test_extract_age_weight_respected(priority_db):
    """claim_pending_task respects workers:extract_age_weight.

    claim_pending_task formula: (10 - vault_priority) * COALESCE(t.priority, 10) + age_bonus
    where age_bonus = seconds_old / age_weight.

    vault-hi (priority 1, fresh):    (10-1)*10 + 0   = 90
    vault-lo (priority 9, 100s old): (10-9)*10 + 100 = 110  → wins when age_weight=1
    """
    old_time = (datetime.utcnow() - timedelta(seconds=100)).strftime('%Y-%m-%d %H:%M:%S')
    with _connect(priority_db) as conn:
        _insert_task(conn, 'hash-hi', 'vault-hi', 'PENDING')        # fresh, score = 90
        _insert_task(conn, 'hash-lo', 'vault-lo', 'PENDING', last_update=old_time)  # old, score = 110
        conn.commit()

    with patch('core.manager.settings') as mock_settings:
        mock_settings.get = lambda key: {'workers:extract_age_weight': 1}.get(key, 3600)
        task = manager.claim_pending_task(priority_db, 'worker-1')

    assert task['file_hash'] == 'hash-lo', "Old low-priority task should overtake fresh high-priority task when age_weight=1"


def test_embed_priority_high_vault_first(priority_db):
    """task from vault-hi (priority 1) is claimed before task from vault-lo (priority 9)
    when both have the same last_update (no aging effect).

    vault-hi score = (10-1) + 0 = 9
    vault-lo score = (10-9) + 0 = 1
    """
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    with _connect(priority_db) as conn:
        _insert_task(conn, 'hash-hi', 'vault-hi', 'EXTRACTED', last_update=now)
        _insert_task(conn, 'hash-lo', 'vault-lo', 'EXTRACTED', last_update=now)
        conn.commit()

    tasks = manager.claim_extracted_tasks(priority_db, 'worker-1', limit=1)
    assert len(tasks) == 1
    assert tasks[0]['file_hash'] == 'hash-hi', "High-priority vault task should be claimed first"


def test_embed_priority_aging_overtakes(priority_db):
    """A vault-lo task aged 3 hours overtakes a fresh vault-hi task.

    vault-hi (fresh):          score = (10-1) + 0      = 9
    vault-lo (3 hours = 10800s): score = (10-9) + 10800/900 = 1 + 12 = 13  -> wins
    Default embed_age_weight=900 is used (schema default, no DB needed).
    """
    old_time = (datetime.utcnow() - timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S')
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    with _connect(priority_db) as conn:
        _insert_task(conn, 'hash-hi', 'vault-hi', 'EXTRACTED', last_update=now)
        _insert_task(conn, 'hash-lo', 'vault-lo', 'EXTRACTED', last_update=old_time)
        conn.commit()

    tasks = manager.claim_extracted_tasks(priority_db, 'worker-1', limit=1)
    assert len(tasks) == 1
    assert tasks[0]['file_hash'] == 'hash-lo', "3-hour-old low-priority task should overtake fresh high-priority task"


def test_embed_priority_orphaned_task(db):
    """A task with a vault_id that has no matching vaults row is claimed without error.
    COALESCE(v.priority, 5) gives it a neutral score of 5."""
    with _connect(db) as conn:
        _insert_task(conn, 'hash-orphan', 'no-such-vault', 'EXTRACTED')
        conn.commit()

    tasks = manager.claim_extracted_tasks(db, 'worker-1', limit=1)
    assert len(tasks) == 1
    assert tasks[0]['file_hash'] == 'hash-orphan'
