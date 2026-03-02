import pytest
import os
from core import manager, ingestor

@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    return db_path

def test_priority_ordering(db, tmp_path):
    docs_path = tmp_path / "docs"
    docs_path.mkdir()

    # Create files with different extensions to trigger different priorities
    (docs_path / "low_priority.mp4").write_text("video")
    (docs_path / "high_priority.txt").write_text("text")
    (docs_path / "medium_priority.pdf").write_text("pdf")

    # Ingest the files
    ingestor.ingest(str(docs_path), db)

    # Claim tasks and check the order
    task1 = manager.claim_pending_task(db, "worker-1")
    assert task1 is not None
    assert task1['file_type'] == 'txt' # Highest priority

    task2 = manager.claim_pending_task(db, "worker-1")
    assert task2 is not None
    assert task2['file_type'] == 'pdf' # Medium priority

    task3 = manager.claim_pending_task(db, "worker-1")
    assert task3 is not None
    assert task3['file_type'] == 'mp4' # Low priority

    task4 = manager.claim_pending_task(db, "worker-1")
    assert task4 is None # No more tasks

