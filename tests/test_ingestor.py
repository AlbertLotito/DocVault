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
