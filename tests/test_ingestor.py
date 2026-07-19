import pytest
import os
from core import manager, ingestor


@pytest.fixture
def env(tmp_path):
    db = str(tmp_path / "test.db")
    manager.init_db(db)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "file1.pdf").write_bytes(b"fake pdf")
    (docs / "file2.txt").write_text("hello world")
    sub = docs / "sub"
    sub.mkdir()
    (sub / "file3.docx").write_bytes(b"fake docx")
    return {"db": db, "docs": str(docs)}


def test_ingest_finds_all_files(env):
    added, moved = ingestor.ingest(env["docs"], env["db"])
    assert added == 3


def test_ingest_skips_duplicates(env):
    ingestor.ingest(env["docs"], env["db"])
    added, moved = ingestor.ingest(env["docs"], env["db"])
    assert added == 0


def test_ingest_assigns_correct_file_type(env):
    ingestor.ingest(env["docs"], env["db"])
    tasks = manager.list_tasks(env["db"])['tasks']
    types = {os.path.basename(t['file_path']): t['file_type'] for t in tasks}
    assert types['file1.pdf'] == 'pdf'
    assert types['file2.txt'] == 'txt'
    assert types['file3.docx'] == 'docx'


def test_ingest_paths_are_normalized(env):
    ingestor.ingest(env["docs"], env["db"])
    tasks = manager.list_tasks(env["db"])['tasks']
    for t in tasks:
        assert '/' not in t['file_path'] or os.sep == '/'


def _make_vault(db_path, vault_id, scan_directory):
    from core.manager import _connect
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
               VALUES (?, ?, ?, 5, 'active', '2024-01-01', '2024-01-01')""",
            (vault_id, vault_id, scan_directory)
        )
        conn.commit()


def test_deleted_file_flags_missing_after_threshold(tmp_path):
    from unittest.mock import patch
    from core.manager import _connect

    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    f = docs / "gone.txt"
    f.write_text("hello")
    _make_vault(db_path, 'vault-a', str(docs))

    with patch('core.ingestor._is_hidden_or_system', return_value=False):
        ingestor.ingest(str(docs), db_path, vault_id='vault-a')

    with _connect(db_path) as conn:
        task = conn.execute("SELECT file_hash, status FROM tasks").fetchone()
    file_hash = task['file_hash']
    assert task['status'] == 'PENDING'

    f.unlink()
    with patch('core.ingestor._is_hidden_or_system', return_value=False):
        for _ in range(3):  # default threshold
            ingestor.ingest(str(docs), db_path, vault_id='vault-a')

    with _connect(db_path) as conn:
        task = conn.execute(
            "SELECT status, pre_missing_status FROM tasks WHERE file_hash = ?", (file_hash,)
        ).fetchone()
    assert task['status'] == 'MISSING'
    assert task['pre_missing_status'] == 'PENDING'


def test_deleted_file_not_flagged_before_threshold(tmp_path):
    from unittest.mock import patch
    from core.manager import _connect

    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    f = docs / "gone.txt"
    f.write_text("hello")
    _make_vault(db_path, 'vault-a', str(docs))

    with patch('core.ingestor._is_hidden_or_system', return_value=False):
        ingestor.ingest(str(docs), db_path, vault_id='vault-a')

    f.unlink()
    with patch('core.ingestor._is_hidden_or_system', return_value=False):
        for _ in range(2):  # one short of default threshold (3)
            ingestor.ingest(str(docs), db_path, vault_id='vault-a')

    with _connect(db_path) as conn:
        task = conn.execute("SELECT status FROM tasks").fetchone()
    assert task['status'] != 'MISSING'


def test_missing_file_restores_on_reappearance(tmp_path):
    from unittest.mock import patch
    from core.manager import _connect

    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    f = docs / "gone.txt"
    f.write_text("hello")
    _make_vault(db_path, 'vault-a', str(docs))

    with patch('core.ingestor._is_hidden_or_system', return_value=False):
        ingestor.ingest(str(docs), db_path, vault_id='vault-a')
        content = f.read_bytes()
        f.unlink()
        for _ in range(3):
            ingestor.ingest(str(docs), db_path, vault_id='vault-a')

        with _connect(db_path) as conn:
            task = conn.execute("SELECT file_hash, status FROM tasks").fetchone()
        assert task['status'] == 'MISSING'
        file_hash = task['file_hash']

        f.write_bytes(content)  # file reappears
        ingestor.ingest(str(docs), db_path, vault_id='vault-a')

    with _connect(db_path) as conn:
        task = conn.execute(
            "SELECT status, pre_missing_status FROM tasks WHERE file_hash = ?", (file_hash,)
        ).fetchone()
    assert task['status'] == 'PENDING'
    assert task['pre_missing_status'] is None


def test_multi_vault_file_not_flagged_while_one_copy_survives(tmp_path):
    from unittest.mock import patch
    from core.manager import _connect
    import hashlib

    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    content = b'shared content'
    expected_hash = hashlib.sha256(content).hexdigest()

    dir_a = tmp_path / "vault_a"
    dir_a.mkdir()
    (dir_a / "photo.jpg").write_bytes(content)
    dir_b = tmp_path / "vault_b"
    dir_b.mkdir()
    (dir_b / "photo.jpg").write_bytes(content)
    _make_vault(db_path, 'vault-a', str(dir_a))
    _make_vault(db_path, 'vault-b', str(dir_b))

    with patch('core.ingestor._is_hidden_or_system', return_value=False):
        ingestor.ingest(str(dir_a), db_path, vault_id='vault-a')
        ingestor.ingest(str(dir_b), db_path, vault_id='vault-b')

        (dir_a / "photo.jpg").unlink()  # gone from vault-a only
        for _ in range(3):
            ingestor.ingest(str(dir_a), db_path, vault_id='vault-a')
            ingestor.ingest(str(dir_b), db_path, vault_id='vault-b')

    with _connect(db_path) as conn:
        task = conn.execute(
            "SELECT status FROM tasks WHERE file_hash = ?", (expected_hash,)
        ).fetchone()
    assert task['status'] != 'MISSING'  # vault-b copy still exists
