# Contributing to DocVault

## 1. Welcome and Philosophy

DocVault aims for *maximum extraction* — every file should yield every meaningful signal it contains, processed locally with no data leaving the machine unless the user explicitly configures it. Contributions that deepen extraction, improve search quality, or harden the system are most welcome.

Whether you are adding a new file-type kernel, improving the search pipeline, or hardening the resource governor, the guiding principle is the same: local-first, privacy-preserving, and extracting as much structured intelligence from documents as possible.

---

## 2. Development Setup

Clone and bootstrap:

```powershell
git clone https://github.com/your-org/docvault.git
cd docvault
.\setup.ps1          # creates venv + installs requirements.txt
```

Or manually:

```bash
python -m venv venv
venv/Scripts/pip install -r requirements.txt   # Windows
# source venv/bin/activate && pip install -r requirements.txt  # Linux/macOS
```

Running tests:

```bash
python -m pytest tests/ -v
```

> **Note:** The test suite does not require a running server or Ollama. Integration checks in `check_*.py` at the repo root do require the server to be running.

---

## 3. The Three-Worker Pipeline

DocVault processes files through three daemon workers. Understanding which layer owns which responsibility is essential before contributing.

### Extraction worker (`workers/extraction_worker.py`)

Claims a `PENDING` task from the queue, selects the highest-priority kernel from the router, calls `kernel.run(file_path, ctx)`, writes extracted text, metadata, and images to `docvault.db`, and advances the task to `EXTRACTED`.

### Embedding worker (`workers/embedding_worker.py`)

Claims an `EXTRACTED` task, chunks the text, calls Ollama to generate embeddings, upserts vectors into the main **LanceDB** store, and advances the task to `EMBEDDED`.

### Art enrichment worker (`workers/art_enrichment_worker.py`)

Runs vision AI against images to identify artworks and enriches results with art metadata (artist, title, medium, provenance signals). It queries a separate, pre-built CLIP embedding table — `art_index` — which lives in its own **LanceDB** table (`embeddings/art_vector_store.py`), distinct from the main document table (`docvault`) but in the same embedded `lancedb_storage/` directory. See [docs/tools/build-art-index.md](docs/tools/build-art-index.md) and [docs/internals/vision.md](docs/internals/vision.md) for how it's built.

### What belongs at each level

| Level | Responsibilities |
|---|---|
| Extractor / kernel | Reading the file, parsing structure, calling OCR or vision APIs, emitting `IngestResult` |
| Worker | Task state management, vector store upsert, priority scheduling, error recovery |

Keep file-format logic in kernels and pipeline orchestration in workers — do not let them bleed into each other.

See [docs/architecture.md](docs/architecture.md) for deeper coverage.

---

## 4. Writing a New Extractor

Every extractor is a Python module (a "kernel") that the registry discovers and the router dispatches to.

### Required structure

**MANIFEST dict** — must be present at module level with these keys:

```python
MANIFEST = {
    "id": "com.yourorg.my-extractor",   # reverse-DNS, globally unique
    "version": "1.0.0",
    "name": "My Extractor",
    "extensions": [".xyz", ".abc"],
}
```

**`extract()` function** — exact signature required:

```python
from core.extractors.base import ExtractorContext, IngestResult

def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    ...
```

### Key rules

- Return `(IngestResult, None)` on success; `(None, "error message")` on failure.
- Never read settings at import time — use `ctx.settings.get(group, key)` inside `extract()`.
- Check `ctx.cancel_token.is_set()` periodically in long-running operations (OCR, vision inference, large file parsing).
- Do not write to any database directly — populate and return an `IngestResult`; the worker owns persistence.
- Keep imports of heavy dependencies (Torch, MediaPipe, Whisper) inside the function body or behind a lazy-load guard so startup remains fast.

Full guide: [docs/extractors/writing-an-extractor.md](docs/extractors/writing-an-extractor.md)

---

## 5. PR Conventions

### Commit message prefixes

| Prefix | Use for |
|---|---|
| `feat:` | New functionality |
| `fix:` | Bug fixes |
| `docs:` | Documentation only |
| `chore:` | Tooling, deps, config |
| `refactor:` | Code restructuring without behaviour change |

### Extractor PRs

Every new extractor must include:

1. A `MANIFEST` dict (see section 4).
2. A typed `extract()` function with exact annotations.
3. An entry in [docs/extractors/catalogue.md](docs/extractors/catalogue.md).

### Bug fix PRs

Include a test that reproduces the bug *before* the fix is applied, so the fix can be verified to address the root cause.

### Settings schema changes

Avoid touching the `settings.db` schema lightly — it survives user data resets and schema changes require migration care. If a new setting is genuinely needed, add it to the schema with a safe default and document the migration path in the PR description.

---

## 6. Code Conventions

These conventions are enforced throughout the codebase. PRs that violate them will be asked to revise.

**Settings at call time**

```python
# Correct — read inside the function
def extract(file_path, ctx):
    timeout = ctx.settings.get('ollama', 'timeout')

# Wrong — read at import/module level
TIMEOUT = settings.get('ollama', 'timeout')
```

**Route ordering**

Register specific routes before parameterised ones. FastAPI matches in declaration order.

```python
# Correct
router.get("/catalog/inspect")   # specific first
router.get("/catalog/{hash}")    # parameterised second
```

**Pydantic v2 optional fields**

```python
# Correct
class MyModel(BaseModel):
    name: str | None = None

# Wrong
class MyModel(BaseModel):
    name: str = None
```

**LanceDB (all vector storage — main store and art_index alike)**

There is no external database service anywhere in DocVault. Both `embeddings/vector_store.py` (main document store, table `docvault`) and `embeddings/art_vector_store.py` (art identification, table `art_index`) are thin wrappers over the same embedded `lancedb_storage/` directory.

```python
# Correct — merge_insert is the upsert path
table.merge_insert("id").when_matched_update_all().when_not_matched_insert_all().execute(records)

# LanceDB's list_tables() returns a ListTablesResponse — use .tables to check membership
if "docvault" in db.list_tables().tables:
    ...
```

**Primary key**

`file_hash` is the SHA-256 of the file's content and is the primary key for all document records. The same file content always maps to the same record regardless of its path on disk. Do not use file paths as identifiers.

---

## 7. Known Limitations and Side Quests

See [docs/internals/SideQuests.md](docs/internals/SideQuests.md) for the current list of known rough edges, deferred work, and open research questions that are good candidates for contribution.
