# LanceDB Migration Plan
# Replace Qdrant with LanceDB (embedded, no external service)

**Goal:** Eliminate the Qdrant dependency (Docker / external service) by replacing it with
LanceDB, which runs in-process as a Python library and stores data in a plain local folder.
Semantic search quality and the public API surface are unchanged.

**Motivation:** Qdrant requires Docker or a separate process. LanceDB is a pip install.

**Architecture:** `embeddings/vector_store.py` is the single abstraction layer for all vector
operations. Rewriting that one file replaces ~90% of the Qdrant surface. The remaining changes
are cleanup: remove Docker restart logic, fix two direct-client bypasses, update settings.

**Tech stack:** Python 3.13, lancedb (pip), pyarrow (pip, already a lancedb dep), existing
VectorStore interface, asyncio.to_thread for the async search path.

**Data schema (LanceDB table `docvault`):**

| Column | Type | Notes |
|---|---|---|
| `id` | string | `"{file_hash}:{chunk_index}"` — natural upsert key |
| `file_hash` | string | SHA-256 of source file |
| `chunk_index` | int32 | Position within document |
| `file_path` | string | Current filesystem path |
| `chunk_text` | string | Raw chunk text for RAG |
| `vector` | fixed_size_list[float32, 768] | Embedding |

**One-time migration:** After deployment, run **Rebuild Index** from the UI.
Existing `qdrant_storage/` data cannot be transferred; all COMPLETED tasks reset to
EXTRACTED and re-embed automatically.

---

## Task 1 — Install LanceDB

**Files:** `venv/` (pip install), requirements if one exists

**Step 1:** Install the package.

```
pip install lancedb
```

**Step 2:** Verify import and version.

```python
import lancedb
print(lancedb.__version__)  # expect 0.20+
```

**Step 3:** If a `requirements.txt` exists, add `lancedb` to it.

**Done when:** `import lancedb` succeeds in the venv with no errors.

---

## Task 2 — Add `lancedb:path` to settings schema; remove Qdrant settings

**Files:**
- Modify: `core/settings.py`
- Modify: `config.ini`

**Step 1:** In `core/settings.py`, in the schema dict, find the `qdrant` group entries:

```python
'qdrant:host': { ... },
'qdrant:port': { ... },
'qdrant:auto_restart': { ... },
'qdrant:restart_cooldown_mins': { ... },
'qdrant:container_name': { ... },
'qdrant:restart_after_failures': { ... },
```

Replace the entire group with a single entry:

```python
'lancedb:path': {
    'type': 'str',
    'default': 'lancedb_storage',
    'label': 'LanceDB storage path',
    'group': 'lancedb',
    'description': 'Directory where LanceDB stores vector data. Relative to project root.',
},
```

**Step 2:** In `config.ini`, remove the `[qdrant]` section entirely and add:

```ini
[lancedb]
path = lancedb_storage
```

**Done when:** `settings.get('lancedb:path')` returns `'lancedb_storage'` and the old
`qdrant:host` key raises a KeyError or returns None.

---

## Task 3 — Rewrite `embeddings/vector_store.py`

**Files:**
- Rewrite: `embeddings/vector_store.py`

This is the core of the migration. Replace the entire file.

**Implementation:**

```python
import os
import lancedb
import pyarrow as pa
import numpy as np
from core.settings import settings


def _db_path() -> str:
    p = settings.get('lancedb:path') or 'lancedb_storage'
    if not os.path.isabs(p):
        p = os.path.join(os.path.dirname(os.path.dirname(__file__)), p)
    os.makedirs(p, exist_ok=True)
    return p


_SCHEMA = pa.schema([
    pa.field('id',          pa.string()),
    pa.field('file_hash',   pa.string()),
    pa.field('chunk_index', pa.int32()),
    pa.field('file_path',   pa.string()),
    pa.field('chunk_text',  pa.string()),
    pa.field('vector',      pa.list_(pa.float32(), 768)),
])


class VectorStore:
    def __init__(self, collection: str = 'docvault', vector_size: int = 768):
        self.collection = collection
        self._vector_size = vector_size
        self._table = self._ensure_table()

    def _ensure_table(self):
        db = lancedb.connect(_db_path())
        if self.collection in db.table_names():
            return db.open_table(self.collection)
        return db.create_table(self.collection, schema=_SCHEMA)

    # ── Write ──────────────────────────────────────────────────────────────────

    def upsert(self, file_hash: str, chunk_index: int,
               vector: list[float], payload: dict):
        self.upsert_batch(file_hash, [{'chunk_index': chunk_index,
                                       'vector': vector, 'payload': payload}])

    def upsert_batch(self, file_hash: str, chunks: list[dict]) -> None:
        if not chunks:
            return
        rows = [
            {
                'id':          f"{file_hash}:{c['chunk_index']}",
                'file_hash':   file_hash,
                'chunk_index': c['chunk_index'],
                'file_path':   c['payload'].get('file_path', ''),
                'chunk_text':  c['payload'].get('chunk_text', ''),
                'vector':      c['vector'],
            }
            for c in chunks
        ]
        (
            self._table.merge_insert('id')
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute(rows)
        )

    def update_path(self, file_hash: str, new_path: str):
        self._table.update(
            where=f"file_hash = '{file_hash}'",
            values={'file_path': new_path},
        )

    def delete_by_hash(self, file_hash: str):
        self._table.delete(f"file_hash = '{_esc(file_hash)}'")

    # ── Read ───────────────────────────────────────────────────────────────────

    def search(self, query_vector: list[float],
               top_k: int = 5, score_threshold: float = None,
               hash_filter: list[str] = None) -> list[dict]:
        if hash_filter is not None and len(hash_filter) == 0:
            return []

        q = (
            self._table.search(query_vector, vector_column_name='vector')
            .metric('cosine')
            .limit(top_k)
        )
        if hash_filter is not None:
            escaped = ', '.join(f"'{_esc(h)}'" for h in hash_filter)
            q = q.where(f"file_hash IN ({escaped})", prefilter=True)

        rows = q.to_list()

        results = []
        for r in rows:
            # LanceDB cosine returns distance (0=identical, 2=opposite).
            # Convert to similarity score: score = 1 - (distance / 2)
            score = 1.0 - (r['_distance'] / 2.0)
            if score_threshold is not None and score < score_threshold:
                continue
            results.append({
                'score':       score,
                'file_hash':   r['file_hash'],
                'file_path':   r['file_path'],
                'chunk_text':  r['chunk_text'],
                'chunk_index': r['chunk_index'],
            })
        return results

    # ── Admin ──────────────────────────────────────────────────────────────────

    def drop_and_recreate(self):
        """Wipe all vectors and start fresh (used by Rebuild Index)."""
        db = lancedb.connect(_db_path())
        if self.collection in db.table_names():
            db.drop_table(self.collection)
        self._table = db.create_table(self.collection, schema=_SCHEMA)

    def count(self) -> int:
        return self._table.count_rows()


def _esc(s: str) -> str:
    """Minimal SQL string escaping for LanceDB filter expressions."""
    return s.replace("'", "''")
```

**Note on cosine distance:** LanceDB returns `_distance` where 0 = identical and 2 = opposite.
The conversion `score = 1 - (distance / 2)` maps this to a [0, 1] similarity score matching
the existing `score_threshold` semantics.

**Done when:** Unit tests in Task 8 pass.

---

## Task 4 — Update `search/semantic.py`

**Files:**
- Modify: `search/semantic.py`

**Step 1:** Remove direct Qdrant client imports and the `AsyncQdrantClient` usage.
The async path will use `asyncio.to_thread` to call the sync `VectorStore.search`.

**Replace the entire file with:**

```python
import asyncio
from embeddings import embedder
from embeddings.vector_store import VectorStore


def _vs() -> VectorStore:
    return VectorStore()


def _threshold() -> float:
    from core.settings import settings
    return float(settings.get('embeddings:score_threshold') or 0.65)


def search(query: str, top_k: int = 5, hash_filter: list[str] = None,
           score_threshold: float = None) -> list[dict]:
    """Semantic search. Supports optional hash_filter for constraint pre-filtering."""
    vector = embedder.embed(query)
    if not vector:
        return []
    threshold = score_threshold if score_threshold is not None else _threshold()
    try:
        return _vs().search(vector, top_k=top_k, score_threshold=threshold,
                            hash_filter=hash_filter)
    except Exception as e:
        from core import logger
        logger.warn(f"[semantic] Vector search unavailable: {e}")
        return []


async def async_search(query: str, top_k: int = 5,
                       hash_filter: list[str] = None) -> list[dict]:
    """Async semantic search for FastAPI routes."""
    vector = await embedder.async_embed(query)
    if not vector:
        return []
    if hash_filter is not None and len(hash_filter) == 0:
        return []
    try:
        return await asyncio.to_thread(
            _vs().search, vector,
            top_k, _threshold(), hash_filter,
        )
    except Exception as e:
        from core import logger
        logger.warn(f"[semantic] Vector search unavailable: {e}")
        return None  # None = failure; [] = success with no results
```

**Done when:** Sync and async search both return results in manual testing.

---

## Task 5 — Update `workers/embedding_worker.py`

**Files:**
- Modify: `workers/embedding_worker.py`

**Step 1:** Delete `_try_restart_qdrant()` entirely (~47 lines).

**Step 2:** Remove the import: `import subprocess` (if only used by `_try_restart_qdrant`).

**Step 3:** Find the `VectorStore(host=..., port=...)` constructor call near the top of
`run()`. Update it to the new no-args signature:

```python
# Before
vs = VectorStore(
    host=r.get('qdrant:host'),
    port=int(r.get('qdrant:port')),
    collection='docvault',
)

# After
vs = VectorStore()
```

**Step 4:** Find the block that tracks consecutive failures and calls `_try_restart_qdrant`.
Remove the failure counter and the restart call entirely. Keep only the existing
`interruptible_sleep` retry logic.

**Step 5:** Remove settings references to `qdrant:restart_after_failures` and
`qdrant:auto_restart` now that the restart logic is gone.

**Done when:** Worker starts cleanly and upserts vectors without Docker or Qdrant running.

---

## Task 6 — Update `core/vault_manager.py`

**Files:**
- Modify: `core/vault_manager.py`

There are two places that construct a `VectorStore` with the old `host/port` signature,
and two `from qdrant_client.models import ...` imports that are no longer needed because
`VectorStore.delete_by_hash()` handles filtering internally.

**Step 1:** Find both blocks that look like:

```python
vs = VectorStore(
    host=settings.get('qdrant:host'),
    port=int(settings.get('qdrant:port')),
    collection='docvault',
)
from qdrant_client.models import Filter, FieldCondition, MatchValue
```

Replace each with:

```python
vs = VectorStore()
```

**Step 2:** Confirm the code that follows each block calls `vs.delete_by_hash(file_hash)`
directly — this is already the pattern used. No further changes needed.

**Done when:** Vault gut and vault delete complete without importing qdrant_client.

---

## Task 7 — Update `core/ingestor.py`

**Files:**
- Modify: `core/ingestor.py`

**Step 1:** Find `_update_qdrant_path()`. It currently creates a `VectorStore(host=..., port=...)`.
Update to:

```python
def _update_vector_path(file_hash, new_path):
    try:
        from embeddings.vector_store import VectorStore
        VectorStore().update_path(file_hash, new_path)
    except Exception as e:
        from core import logger
        logger.error(f"[vector] Path update failed for {file_hash[:8]}: {e}")
```

**Step 2:** Update the call site from `_update_qdrant_path(...)` to `_update_vector_path(...)`.

**Done when:** File moves/renames update the stored path in LanceDB correctly.

---

## Task 8 — Update `api/routes/utils.py`

**Files:**
- Modify: `api/routes/utils.py`

**Step 1:** Remove the import at the top:

```python
from utils.qdrant_check import check_qdrant_health
from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models
```

**Step 2:** Replace the `reindex_all()` route body with:

```python
@router.post("/utils/reindex")
def reindex_all():
    """
    Wipe the vector collection and reset all COMPLETED tasks to EXTRACTED
    so the embedding worker re-embeds everything from scratch.
    """
    from api.main import DB_PATH
    from embeddings.vector_store import VectorStore

    VectorStore().drop_and_recreate()

    import sqlite3
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        cur = conn.execute(
            "UPDATE tasks SET status='EXTRACTED', worker_id=NULL WHERE status='COMPLETED'"
        )
        reset_count = cur.rowcount

    return {'status': 'ok', 'reset_tasks': reset_count}
```

**Step 3:** Replace the `qdrant_check` endpoint with a LanceDB health check:

```python
@router.get("/utils/qdrant_check")
def run_qdrant_check():
    """Health check for the vector store."""
    try:
        from embeddings.vector_store import VectorStore
        vs = VectorStore()
        count = vs.count()
        return {'status': 'ok', 'vectors': count}
    except Exception as e:
        return {'status': 'error', 'detail': str(e)}
```

Note: keep the route path as `/utils/qdrant_check` to avoid breaking any frontend
that calls it — the response shape is compatible.

**Step 4:** Find the diagnostics block (~line 404) that queries `qdrant_errs` from the
worker_errors table and builds a "Qdrant embedding failures" action card. Update the
label strings from "Qdrant" to "embedding" but keep the logic — the error records still
exist in logs.db with the same shape.

**Done when:** `/utils/reindex` works end-to-end; `/utils/qdrant_check` returns vector count.

---

## Task 9 — Update `api/routes/vaults.py`

**Files:**
- Modify: `api/routes/vaults.py`

**Step 1:** Find the `VectorStore(host=..., port=...)` constructor call (around line 160).
Update to `VectorStore()`.

**Step 2:** Remove any `from qdrant_client...` import in this file.

**Done when:** File has no qdrant_client imports.

---

## Task 10 — Update utility scripts

**Files:**
- Modify or delete: `utils/qdrant_check.py`
- Modify or delete: `utils/connection_test.py`

**Step 1:** Replace the body of `utils/qdrant_check.py` with a LanceDB check:

```python
from embeddings.vector_store import VectorStore

async def check_qdrant_health():
    try:
        vs = VectorStore()
        return {'status': 'ok', 'vectors': vs.count()}
    except Exception as e:
        return {'status': 'error', 'detail': str(e)}
```

**Step 2:** In `utils/connection_test.py`, remove or replace the Qdrant connection test
block with a LanceDB open/count check.

**Done when:** No file in `utils/` imports from `qdrant_client`.

---

## Task 11 — Update tests

**Files:**
- Modify: `tests/test_embeddings.py`
- Modify: `tests/test_embedding_worker.py`

**Step 1:** In `test_embeddings.py`, find any mocks for `QdrantClient` or `qdrant_client`.
Replace with mocks for `lancedb.connect` or `VectorStore` directly.

**Step 2:** In `test_embedding_worker.py`, remove mocks for `_try_restart_qdrant` and
`docker restart`. Update `VectorStore` constructor mock to match new no-args signature.

**Step 3:** Run the full test suite:

```
python -m pytest tests/ -x -q
```

**Done when:** All tests pass with no qdrant_client imports anywhere in test files.

---

## Task 12 — Smoke test and Rebuild Index

**Step 1:** Start the server. Confirm it starts without errors and without Docker/Qdrant running.

**Step 2:** Open the UI. Navigate to Utils → Rebuild Index. Click it. Confirm:
- Returns `{ status: ok, reset_tasks: N }` where N > 0 if tasks exist
- Embedding worker begins processing EXTRACTED tasks
- `/utils/qdrant_check` returns `{ status: ok, vectors: N }` as N grows

**Step 3:** Run a semantic search query. Confirm results are returned.

**Step 4:** Confirm `lancedb_storage/` directory was created and contains LanceDB files.

**Step 5:** Optionally delete `qdrant_storage/` (it is now dead weight).

---

## Summary of files changed

| File | Change |
|---|---|
| `embeddings/vector_store.py` | Full rewrite — LanceDB implementation |
| `search/semantic.py` | Full rewrite — remove AsyncQdrantClient, use asyncio.to_thread |
| `workers/embedding_worker.py` | Delete `_try_restart_qdrant`, update constructor |
| `core/vault_manager.py` | Update VectorStore constructor (×2), remove qdrant_client imports |
| `core/ingestor.py` | Rename `_update_qdrant_path` → `_update_vector_path`, update constructor |
| `api/routes/utils.py` | Rewrite `reindex_all`, replace `qdrant_check`, remove qdrant imports |
| `api/routes/vaults.py` | Update VectorStore constructor, remove qdrant_client import |
| `core/settings.py` | Remove 6 qdrant: settings, add `lancedb:path` |
| `config.ini` | Remove `[qdrant]`, add `[lancedb]` |
| `utils/qdrant_check.py` | Replace with LanceDB health check |
| `utils/connection_test.py` | Remove Qdrant connection test |
| `tests/test_embeddings.py` | Update mocks |
| `tests/test_embedding_worker.py` | Update mocks, remove restart mock |

## Files deleted / made obsolete

| File | Action |
|---|---|
| `qdrant_storage/` | Delete after Rebuild Index completes |

## New dependencies

| Package | Purpose |
|---|---|
| `lancedb` | Embedded vector store |
| `pyarrow` | LanceDB dependency, likely already installed |
