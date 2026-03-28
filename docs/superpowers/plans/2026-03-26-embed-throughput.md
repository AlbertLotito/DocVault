# Embedding Throughput Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate GPU idle time between documents by batching chunks from multiple documents into one Ollama call, and add a configurable worker concurrency setting for systems with OLLAMA_NUM_PARALLEL > 1.

**Architecture:** Two orthogonal improvements. (1) Cross-document batching: `claim_extracted_tasks(limit=N)` claims N tasks at once; `process_task_batch()` chunks all N documents, sends all chunks in a single `embed_batch()` call, then upserts per-document. (2) Concurrency: `workers:embed_concurrency` setting controls how many embedding worker threads `run.py` starts; each gets a unique worker ID so DB claiming is collision-free.

**Tech Stack:** Python, SQLite (core/manager.py), FastAPI worker threads (run.py), ollama Python client, qdrant-client.

---

## Chunk 1: Settings + claim_extracted_tasks

### Task 1: Add `workers:embed_batch_size` and `workers:embed_concurrency` to settings schema

**Files:**
- Modify: `core/settings.py` (after line 123, inside the `embeddings` block)
- Test: `tests/test_embed_throughput_settings.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_embed_throughput_settings.py`:

```python
from core.settings import SETTINGS_SCHEMA


def test_embed_batch_size_setting_exists():
    assert 'workers:embed_batch_size' in SETTINGS_SCHEMA
    s = SETTINGS_SCHEMA['workers:embed_batch_size']
    assert s['type'] == 'int'
    assert s['default'] == 8


def test_embed_concurrency_setting_exists():
    assert 'workers:embed_concurrency' in SETTINGS_SCHEMA
    s = SETTINGS_SCHEMA['workers:embed_concurrency']
    assert s['type'] == 'int'
    assert s['default'] == 1
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_embed_throughput_settings.py -v
```
Expected: `FAILED — KeyError: 'workers:embed_batch_size'`

- [ ] **Step 3: Add settings to `core/settings.py`**

Locate the line:
```python
            'embeddings:score_threshold': {
```
(currently line ~120). After the closing `},` for that entry (line ~123), add:

```python
            # Worker throughput
            'workers:embed_batch_size': {
                'type': 'int', 'default': 8, 'label': 'Embed batch size (docs)', 'group': 'embeddings',
                'description': 'Number of documents whose chunks are accumulated into a single Ollama embedding call. '
                               'Larger values keep the GPU busier between documents but use more memory. '
                               'Recommended: 4–16. Has no effect when the queue is nearly empty.',
            },
            'workers:embed_concurrency': {
                'type': 'int', 'default': 1, 'label': 'Embed worker threads', 'group': 'embeddings',
                'description': 'Number of parallel embedding worker threads. Set to 2–4 only if Ollama is '
                               'configured with OLLAMA_NUM_PARALLEL > 1 and you have sufficient VRAM. '
                               'Increasing this without OLLAMA_NUM_PARALLEL > 1 adds SQLite contention '
                               'with no throughput benefit. Requires restart.',
            },
```

Note: `SETTINGS_SCHEMA` is the dict name — verify the exact variable name used in `core/settings.py` and match it.

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_embed_throughput_settings.py -v
```
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add core/settings.py tests/test_embed_throughput_settings.py
git commit -m "feat(settings): add workers:embed_batch_size and workers:embed_concurrency"
```

---

### Task 2: Add `claim_extracted_tasks(db_path, worker_id, limit)` to `core/manager.py`

**Files:**
- Modify: `core/manager.py` (after `claim_extracted_task`, currently line ~634)
- Test: `tests/test_embed_throughput_settings.py` (extend with new class)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_embed_throughput_settings.py`:

```python
import pytest
from core import manager


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    return db_path


def _insert_extracted(db, file_hash, file_path='test.pdf', text='hello'):
    manager.insert_task(db, file_hash, file_path, 'pdf')
    manager.complete_extraction(db, file_hash, text=text, status='EXTRACTED')


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
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_embed_throughput_settings.py::TestClaimExtractedTasks -v
```
Expected: `FAILED — AttributeError: module 'core.manager' has no attribute 'claim_extracted_tasks'`

- [ ] **Step 3: Add `claim_extracted_tasks` to `core/manager.py`**

After `claim_extracted_task` (currently ending around line 634), add:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_embed_throughput_settings.py -v
```
Expected: `6 passed` (2 settings + 4 claim tests)

- [ ] **Step 5: Run full test suite to check for regressions**

```
pytest tests/test_manager.py tests/test_embeddings.py tests/test_embedding_worker.py -q
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add core/manager.py tests/test_embed_throughput_settings.py
git commit -m "feat(manager): add claim_extracted_tasks for batch embedding"
```

---

## Chunk 2: Batch worker + multi-thread startup

### Task 3: Add `process_task_batch()` and update the run loop in `workers/embedding_worker.py`

**Files:**
- Modify: `workers/embedding_worker.py`
- Modify: `tests/test_embedding_worker.py`

The existing `process_task()` is kept intact (used nowhere else, but good for reference and single-task recovery paths). The new `process_task_batch()` handles a list of tasks in one Ollama call.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_embedding_worker.py`:

```python
from unittest.mock import patch, MagicMock
from workers import embedding_worker


@patch('workers.embedding_worker.manager.update_task_status')
@patch('workers.embedding_worker.manager.update_task_progress')
@patch('workers.embedding_worker.embedder.embed_batch')
@patch('workers.embedding_worker.chunker.chunk')
def test_process_task_batch_sends_one_ollama_call(mock_chunk, mock_embed, mock_progress, mock_status):
    """All chunks from all docs go in a single embed_batch call."""
    mock_chunk.side_effect = [['c1', 'c2'], ['c3']]  # doc1=2 chunks, doc2=1 chunk
    mock_embed.return_value = [[0.1]*768, [0.2]*768, [0.3]*768]
    mock_vs = MagicMock()

    tasks = [
        {'file_hash': 'h1', 'file_path': '/a.pdf', 'file_type': 'pdf', 'extracted_text': 'text1'},
        {'file_hash': 'h2', 'file_path': '/b.pdf', 'file_type': 'pdf', 'extracted_text': 'text2'},
    ]
    embedding_worker.process_task_batch('test.db', tasks, mock_vs)

    mock_embed.assert_called_once()
    inputs = mock_embed.call_args[0][0]
    assert len(inputs) == 3  # 2 + 1 chunks total

    assert mock_vs.upsert_batch.call_count == 2  # one per doc
    mock_status.assert_any_call('test.db', 'h1', status='COMPLETED')
    mock_status.assert_any_call('test.db', 'h2', status='COMPLETED')


@patch('workers.embedding_worker.manager.update_task_status')
@patch('workers.embedding_worker.manager.update_task_progress')
@patch('workers.embedding_worker.embedder.embed_batch')
@patch('workers.embedding_worker.chunker.chunk')
def test_process_task_batch_completes_empty_docs_without_embed(mock_chunk, mock_embed, mock_progress, mock_status):
    """Empty-text tasks are marked COMPLETED immediately, no embed call."""
    mock_chunk.return_value = []
    mock_vs = MagicMock()

    tasks = [
        {'file_hash': 'h1', 'file_path': '/a.pdf', 'file_type': 'pdf', 'extracted_text': ''},
    ]
    embedding_worker.process_task_batch('test.db', tasks, mock_vs)

    mock_embed.assert_not_called()
    mock_vs.upsert_batch.assert_not_called()
    mock_status.assert_called_once_with('test.db', 'h1', 'COMPLETED')


@patch('workers.embedding_worker.manager.update_task_status')
@patch('workers.embedding_worker.manager.update_task_progress')
@patch('workers.embedding_worker.embedder.embed_batch')
@patch('workers.embedding_worker.chunker.chunk')
def test_process_task_batch_skips_none_vectors(mock_chunk, mock_embed, mock_progress, mock_status):
    """None vectors (embed failure) are excluded from upsert, task still completes."""
    mock_chunk.return_value = ['c1', 'c2', 'c3']
    mock_embed.return_value = [[0.1]*768, None, [0.3]*768]
    mock_vs = MagicMock()

    tasks = [{'file_hash': 'h1', 'file_path': '/a.pdf', 'file_type': 'pdf', 'extracted_text': 'text'}]
    embedding_worker.process_task_batch('test.db', tasks, mock_vs)

    batch = mock_vs.upsert_batch.call_args[0][1]
    assert len(batch) == 2
    assert batch[0]['chunk_index'] == 0
    assert batch[1]['chunk_index'] == 2
    mock_status.assert_called_once_with('test.db', 'h1', status='COMPLETED')


@patch('workers.embedding_worker.manager.update_task_status')
@patch('workers.embedding_worker.manager.update_task_progress')
@patch('workers.embedding_worker.embedder.embed_batch')
@patch('workers.embedding_worker.chunker.chunk')
def test_process_task_batch_resets_to_extracted_on_qdrant_failure(mock_chunk, mock_embed, mock_progress, mock_status):
    """If Qdrant upsert raises, ALL non-empty tasks are reset to EXTRACTED and exception re-raises."""
    mock_chunk.side_effect = [['c1'], ['c2']]
    mock_embed.return_value = [[0.1]*768, [0.2]*768]
    mock_vs = MagicMock()
    mock_vs.upsert_batch.side_effect = Exception("Qdrant down")

    tasks = [
        {'file_hash': 'h1', 'file_path': '/a.pdf', 'file_type': 'pdf', 'extracted_text': 'text1'},
        {'file_hash': 'h2', 'file_path': '/b.pdf', 'file_type': 'pdf', 'extracted_text': 'text2'},
    ]
    with pytest.raises(Exception, match="Qdrant down"):
        embedding_worker.process_task_batch('test.db', tasks, mock_vs)

    all_calls = mock_status.call_args_list
    completed = [c for c in all_calls if 'COMPLETED' in str(c)]
    extracted = [c for c in all_calls if 'EXTRACTED' in str(c)]
    assert len(completed) == 0
    assert len(extracted) == 2
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_embedding_worker.py::test_process_task_batch_sends_one_ollama_call -v
```
Expected: `FAILED — AttributeError: module 'workers.embedding_worker' has no attribute 'process_task_batch'`

- [ ] **Step 3: Add `process_task_batch()` and update `run()` in `workers/embedding_worker.py`**

Add `process_task_batch` after `process_task` (around line 125):

```python
def process_task_batch(db_path, tasks, vs):
    """Process a list of EMBEDDING tasks: chunk all → one embed call → upsert per doc."""
    # Phase 1: chunk all documents
    # each entry: (task, chunk_index, chunk_text, embed_input_string)
    all_entries = []
    empty_hashes = []

    for task in tasks:
        file_hash = task['file_hash']
        file_path = task['file_path']
        filename  = os.path.basename(file_path)
        text = task.get('extracted_text') or ''
        chunks = chunker.chunk(text)
        if not chunks:
            empty_hashes.append(file_hash)
            continue
        for i, chunk_text in enumerate(chunks):
            all_entries.append((task, i, chunk_text, f"{filename}\n{chunk_text}"))

    for fh in empty_hashes:
        manager.update_task_status(db_path, fh, 'COMPLETED')

    if not all_entries:
        return

    n_docs   = len({e[0]['file_hash'] for e in all_entries})
    n_chunks = len(all_entries)
    logger.info(f"Embedding batch: {n_docs} doc(s), {n_chunks} chunk(s)")

    # Phase 2: one Ollama call for all chunks
    inputs  = [e[3] for e in all_entries]
    vectors = embedder.embed_batch(inputs)

    # Phase 3: group vectors back by document
    doc_batches: dict = {}
    doc_tasks:   dict = {}
    for (task, chunk_index, chunk_text, _), vector in zip(all_entries, vectors):
        fh = task['file_hash']
        doc_tasks[fh]  = task
        if fh not in doc_batches:
            doc_batches[fh] = []
        if vector is not None:
            doc_batches[fh].append({
                'chunk_index': chunk_index,
                'vector':      vector,
                'payload': {
                    'file_path':   task['file_path'],
                    'chunk_text':  chunk_text,
                    'chunk_index': chunk_index,
                },
            })

    # Phase 4: upsert all docs, then mark complete.
    # If any upsert fails, reset ALL non-empty docs to EXTRACTED so the
    # outer loop detects the Qdrant failure and reconnects.
    try:
        for fh, batch in doc_batches.items():
            if batch:
                vs.upsert_batch(fh, batch)
    except Exception as e:
        logger.error(f"Qdrant upsert failed: {e}. Resetting batch to EXTRACTED.")
        for fh in doc_batches:
            try:
                manager.update_task_status(db_path, fh, status='EXTRACTED')
            except Exception as e2:
                logger.error(f"Could not reset {fh[:8]} to EXTRACTED: {e2}")
        raise

    for fh in doc_batches:
        manager.update_task_status(db_path, fh, status='COMPLETED')
    logger.info(f"  Batch complete: {len(doc_batches)} doc(s).")
```

Then update the `run()` loop. Replace the block that claims/processes a single task:

**Find this in `run()`:**
```python
        try:
            task = manager.claim_extracted_task(db_path, worker_id)
        except Exception as e:
            logger.warn(f"Embedding worker DB contention, retrying in 5s: {e}")
            time.sleep(5)
            continue

        if task:
            try:
                process_task(db_path, task, vs)
            except Exception as e:
                # process_task re-raises on Qdrant failure after resetting to EXTRACTED.
                # Reset vs so next iteration re-connects.
                logger.error(f"Qdrant connection lost. Will reconnect in 10s. Error: {e}")
                vs = None
        else:
            try:
                interruptible_sleep(db_path, 10)
            except Exception:
                time.sleep(10)
```

**Replace with:**
```python
        try:
            batch_size = int(settings.get('workers:embed_batch_size') or 8)
            tasks = manager.claim_extracted_tasks(db_path, worker_id, limit=batch_size)
        except Exception as e:
            logger.warn(f"Embedding worker DB contention, retrying in 5s: {e}")
            time.sleep(5)
            continue

        if tasks:
            try:
                process_task_batch(db_path, tasks, vs)
            except Exception as e:
                logger.error(f"Qdrant connection lost. Will reconnect in 10s. Error: {e}")
                vs = None
        else:
            try:
                interruptible_sleep(db_path, 10)
            except Exception:
                time.sleep(10)
```

Note: `settings` is already imported at the top of `embedding_worker.py` from `core.settings`.

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_embedding_worker.py -v
```
Expected: all pass (3 original + 4 new = 7 total)

- [ ] **Step 5: Run full embedding test suite**

```
pytest tests/test_embeddings.py tests/test_embedding_worker.py -q
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add workers/embedding_worker.py tests/test_embedding_worker.py
git commit -m "feat(worker): batch cross-document embedding — one Ollama call per batch"
```

---

### Task 4: Start N embedding workers in `run.py`

**Files:**
- Modify: `run.py` (around line 147–151 where `managed_workers` is defined)
- Test: `tests/test_embed_throughput_settings.py` (extend with a run.py test)

Introduce a small helper `_make_embed_workers(db_path, concurrency)` in `run.py` that builds the worker entries list. This keeps the test real — it calls actual `run.py` code, not a manual reconstruction.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_embed_throughput_settings.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

```
pytest tests/test_embed_throughput_settings.py::TestEmbedWorkerStartup -v
```
Expected: `FAILED — AttributeError: module 'run' has no attribute '_make_embed_workers'`

- [ ] **Step 3: Update `run.py`**

**3a.** Add `import socket` at the top of `run.py` (with the other stdlib imports):
```python
import socket
```

**3b.** Add a module-level helper before `ingestion_worker_run` (around line 17):

```python
def _make_embed_workers(db_path: str, concurrency: int) -> list:
    """Return watchdog entries for N embedding worker threads."""
    entries = []
    for i in range(max(1, concurrency)):
        wid = f"embed-{socket.gethostname()}-{os.getpid()}-{i}"
        entries.append((f"embedding-{i}", embedding_worker.run, (db_path, None, wid)))
    return entries
```

**3c.** Find the `managed_workers` list (around line 147):
```python
    managed_workers = [
        ('ingestion',   ingestion_worker_run,        (DB_PATH,)),
        ('extraction',  extraction_worker.run,        (DB_PATH, None)),
        ('embedding',   embedding_worker.run,         (DB_PATH, None)),
        ('art',         art_enrichment_worker.run,    (DB_PATH, None)),
    ]
```

Replace with:
```python
    embed_concurrency = max(1, int(settings.get('workers:embed_concurrency') or 1))
    managed_workers = [
        ('ingestion',   ingestion_worker_run,        (DB_PATH,)),
        ('extraction',  extraction_worker.run,        (DB_PATH, None)),
        ('art',         art_enrichment_worker.run,    (DB_PATH, None)),
    ]
    managed_workers.extend(_make_embed_workers(DB_PATH, embed_concurrency))
```

- [ ] **Step 4: Run all tests**

```
pytest tests/test_embed_throughput_settings.py tests/test_embeddings.py tests/test_embedding_worker.py -v
```
Expected: all pass

- [ ] **Step 5: Run broader test suite to check for regressions**

```
pytest tests/ -q --ignore=tests/rigs 2>&1 | tail -15
```
Expected: no new failures

- [ ] **Step 6: Commit**

```bash
git add run.py tests/test_embed_throughput_settings.py
git commit -m "feat(run): configurable embedding worker concurrency via workers:embed_concurrency"
```
