from core.settings import SETTINGS_SCHEMA


def test_embed_batch_size_setting_exists():
    assert 'workers:embed_batch_size' in SETTINGS_SCHEMA
    s = SETTINGS_SCHEMA['workers:embed_batch_size']
    assert s['type'] == 'int'
    assert s['default'] == 8
    assert 'label' in s and len(s['label']) > 0
    assert s['group'] == 'embeddings'
    assert 'description' in s and len(s['description']) > 0


def test_embed_concurrency_setting_exists():
    assert 'workers:embed_concurrency' in SETTINGS_SCHEMA
    s = SETTINGS_SCHEMA['workers:embed_concurrency']
    assert s['type'] == 'int'
    assert s['default'] == 1
    assert 'label' in s and len(s['label']) > 0
    assert s['group'] == 'embeddings'
    assert 'description' in s and len(s['description']) > 0


import pytest
from core import manager


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    return db_path


def _insert_extracted(db, file_hash, file_path='test.pdf', text='hello'):
    manager.insert_task(db, file_hash, file_path, 'pdf')
    manager.complete_extraction(db, file_hash, status='EXTRACTED', text=text)


class TestClaimExtractedTasks:
    def test_claims_up_to_limit(self, db):
        for i in range(5):
            _insert_extracted(db, f'hash{i}', f'/docs/f{i}.pdf')
        tasks = manager.claim_extracted_tasks(db, 'worker-1', limit=3)
        assert len(tasks) == 3
        for t in tasks:
            row = manager.get_task(db, t['file_hash'])
            assert row['status'] == 'EMBEDDING'

    def test_claims_fewer_when_queue_smaller(self, db):
        _insert_extracted(db, 'only1', '/docs/only1.pdf')
        tasks = manager.claim_extracted_tasks(db, 'worker-1', limit=8)
        assert len(tasks) == 1

    def test_returns_empty_list_when_no_tasks(self, db):
        tasks = manager.claim_extracted_tasks(db, 'worker-1', limit=8)
        assert tasks == []

    def test_sets_worker_id(self, db):
        _insert_extracted(db, 'hash1', '/docs/f1.pdf')
        tasks = manager.claim_extracted_tasks(db, 'my-worker', limit=1)
        row = manager.get_task(db, 'hash1')
        assert row['worker_id'] == 'my-worker'


import run as run_module


class TestEmbedWorkerStartup:
    def test_make_embed_workers_returns_n_entries(self):
        """_make_embed_workers produces one (name, fn, args) tuple per concurrency level."""
        workers = run_module._make_embed_workers('test.db', 2)
        assert len(workers) == 2
        assert workers[0][0] == 'embedding-0'
        assert workers[1][0] == 'embedding-1'
        # db_path is threaded through to each worker's args
        assert workers[0][2][0] == 'test.db'
        assert workers[1][2][0] == 'test.db'

    def test_make_embed_workers_minimum_one(self):
        """concurrency=0 is clamped to 1."""
        workers = run_module._make_embed_workers('test.db', 0)
        assert len(workers) == 1
        assert workers[0][0] == 'embedding-0'
