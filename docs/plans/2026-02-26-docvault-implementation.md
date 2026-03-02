# DocVault Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a general-purpose document intelligence suite with extraction, full-text search, vector embeddings, RAG querying, and a web GUI control panel.

**Architecture:** Modular extractor pipeline (one module per file type, routed by extension) feeds a two-phase worker system (extract → embed). FastAPI serves a Tailwind web UI with dashboard, catalog browser, and search/RAG interface. SQLite handles task tracking and FTS5 full-text search; Qdrant handles vector search.

**Tech Stack:** Python 3.11+, FastAPI, SQLite FTS5, Qdrant, Ollama (nomic-embed-text + llm), Whisper large-v3 (CUDA), pypdf, python-docx, openpyxl, python-pptx, pytesseract, ffmpeg-python, Tailwind CSS

---

## Task 1: Project Scaffold

**Files:**
- Create: `E:\DocVault\requirements.txt`
- Create: `E:\DocVault\config.ini`
- Create: `E:\DocVault\pytest.ini`
- Create: `E:\DocVault\.gitignore`
- Create all package `__init__.py` stubs

**Step 1: Create the folder tree**

```
mkdir E:\DocVault\core
mkdir E:\DocVault\extractors
mkdir E:\DocVault\embeddings
mkdir E:\DocVault\llm
mkdir E:\DocVault\search
mkdir E:\DocVault\workers
mkdir E:\DocVault\api
mkdir E:\DocVault\api\routes
mkdir E:\DocVault\frontend
mkdir E:\DocVault\frontend\static
mkdir E:\DocVault\tests
```

**Step 2: Create `E:\DocVault\requirements.txt`**

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
pypdf[image]>=4.0.0
python-docx>=1.1.0
openpyxl>=3.1.0
python-pptx>=0.6.23
pytesseract>=0.3.10
Pillow>=10.0.0
openai-whisper
torch>=2.0.0
torchaudio>=2.0.0
ffmpeg-python>=0.2.0
qdrant-client>=1.9.0
ollama>=0.2.0
httpx>=0.27.0
python-multipart>=0.0.9
pytest>=8.0.0
pytest-asyncio>=0.23.0
httpx>=0.27.0
```

**Step 3: Create `E:\DocVault\config.ini`**

```ini
[paths]
scan_directory = C:\Users\Albert\Documents

[database]
sqlite_path = E:\DocVault\docvault.db

[qdrant]
host = 192.168.1.11
port = 6333
collection = docvault

[ollama]
host = http://localhost:11434
embed_model = nomic-embed-text
chat_model = llama3

[llm]
provider = ollama
```

**Step 4: Create `E:\DocVault\pytest.ini`**

```ini
[pytest]
testpaths = tests
asyncio_mode = auto
```

**Step 5: Create `E:\DocVault\.gitignore`**

```
venv/
__pycache__/
*.pyc
*.db
*.db-shm
*.db-wal
.env
docvault.db
```

**Step 6: Create all empty `__init__.py` files**

Create empty `__init__.py` in: `core/`, `extractors/`, `embeddings/`, `llm/`, `search/`, `workers/`, `api/`, `api/routes/`, `tests/`

**Step 7: Create venv and install dependencies**

```
cd E:\DocVault
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```

Note: `torch` with CUDA — if the CPU version installs, replace with:
```
venv\Scripts\pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
```

**Step 8: Verify install**

```
cd E:\DocVault && venv\Scripts\python -c "import fastapi, pypdf, docx, qdrant_client; print('OK')"
```

Expected: `OK`

**Step 9: Init git**

```
cd E:\DocVault
git init
git add .
git commit -m "chore: project scaffold"
```

---

## Task 2: Core DB Layer (`core/manager.py`)

**Files:**
- Create: `E:\DocVault\core\manager.py`
- Create: `E:\DocVault\tests\test_manager.py`

**Step 1: Write the failing tests first**

Create `E:\DocVault\tests\test_manager.py`:

```python
import pytest
import os
import tempfile
from core import manager


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    return db_path


def test_init_creates_tables(db):
    import sqlite3
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    conn.close()
    assert 'tasks' in tables
    assert 'extracted_images' in tables
    assert 'settings' in tables


def test_insert_task(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    task = manager.get_task(db, 'abc123')
    assert task['file_hash'] == 'abc123'
    assert task['file_path'] == '/docs/test.pdf'
    assert task['file_type'] == 'pdf'
    assert task['status'] == 'PENDING'


def test_insert_task_deduplicates(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')  # duplicate
    tasks = manager.list_tasks(db)
    assert len(tasks) == 1


def test_update_task_status(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    manager.update_task_status(db, 'abc123', 'EXTRACTING')
    task = manager.get_task(db, 'abc123')
    assert task['status'] == 'EXTRACTING'


def test_complete_task_stores_text(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    manager.complete_extraction(db, 'abc123', text='Hello world', status='EXTRACTED')
    task = manager.get_task(db, 'abc123')
    assert task['status'] == 'EXTRACTED'
    assert task['extracted_text'] == 'Hello world'


def test_complete_task_stores_error(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    manager.complete_extraction(db, 'abc123', status='ERROR', error='File not found')
    task = manager.get_task(db, 'abc123')
    assert task['status'] == 'ERROR'
    assert 'File not found' in task['error_log']


def test_claim_pending_task(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    task = manager.claim_pending_task(db, 'worker-1')
    assert task is not None
    assert task['file_hash'] == 'abc123'
    assert task['status'] == 'PROCESSING'


def test_claim_returns_none_when_empty(db):
    task = manager.claim_pending_task(db, 'worker-1')
    assert task is None


def test_get_stats(db):
    manager.insert_task(db, 'a', '/docs/a.pdf', 'pdf')
    manager.insert_task(db, 'b', '/docs/b.txt', 'txt')
    manager.complete_extraction(db, 'b', text='hi', status='EXTRACTED')
    stats = manager.get_stats(db)
    assert stats['total'] == 2
    assert stats['pending'] == 1
    assert stats['extracted'] == 1


def test_insert_extracted_image(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    result = manager.insert_extracted_image(db, 'abc123', {
        'file_path': '/docs/test/page_001_img_001.png',
        'page_num': 1, 'image_index': 1, 'width': 816, 'height': 1056
    })
    assert result is True


def test_fts_search(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    manager.complete_extraction(db, 'abc123', text='The quick brown fox', status='EXTRACTED')
    results = manager.fts_search(db, 'brown fox')
    assert len(results) == 1
    assert results[0]['file_hash'] == 'abc123'


def test_get_pause_state_defaults_false(db):
    assert manager.get_pause_state(db) is False


def test_set_pause_state(db):
    manager.set_pause_state(db, True)
    assert manager.get_pause_state(db) is True
    manager.set_pause_state(db, False)
    assert manager.get_pause_state(db) is False
```

**Step 2: Run tests to confirm they fail**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_manager.py -v
```

Expected: `ImportError` — module doesn't exist yet.

**Step 3: Create `E:\DocVault\core\manager.py`**

```python
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
```

**Step 4: Run tests to confirm they pass**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_manager.py -v
```

Expected: `15 passed`

**Step 5: Commit**

```
git add core/manager.py tests/test_manager.py
git commit -m "feat: core DB layer with SQLite FTS5"
```

---

## Task 3: Core Ingestor (`core/ingestor.py`)

**Files:**
- Create: `E:\DocVault\core\ingestor.py`
- Create: `E:\DocVault\tests\test_ingestor.py`

**Step 1: Write the failing tests**

Create `E:\DocVault\tests\test_ingestor.py`:

```python
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
    added = ingestor.ingest(env["docs"], env["db"])
    assert added == 3


def test_ingest_skips_duplicates(env):
    ingestor.ingest(env["docs"], env["db"])
    added = ingestor.ingest(env["docs"], env["db"])
    assert added == 0


def test_ingest_assigns_correct_file_type(env):
    ingestor.ingest(env["docs"], env["db"])
    tasks = manager.list_tasks(env["db"])
    types = {os.path.basename(t['file_path']): t['file_type'] for t in tasks}
    assert types['file1.pdf'] == 'pdf'
    assert types['file2.txt'] == 'txt'
    assert types['file3.docx'] == 'docx'


def test_ingest_paths_are_normalized(env):
    ingestor.ingest(env["docs"], env["db"])
    tasks = manager.list_tasks(env["db"])
    for t in tasks:
        assert '/' not in t['file_path'] or os.sep == '/'
```

**Step 2: Run to confirm failure**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_ingestor.py -v
```

Expected: `ImportError`

**Step 3: Create `E:\DocVault\core\ingestor.py`**

```python
import os
import hashlib
from core import manager


def _sha256(file_path):
    h = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def ingest(directory, db_path):
    """
    Walk directory, hash each file, insert new tasks.
    Returns count of newly added files.
    """
    added = 0
    for root, _, files in os.walk(directory):
        for name in files:
            file_path = os.path.normpath(os.path.join(root, name))
            ext = os.path.splitext(name)[1].lstrip('.').lower()
            try:
                file_hash = _sha256(file_path)
                if not manager.hash_exists(db_path, file_hash):
                    manager.insert_task(db_path, file_hash, file_path, ext)
                    added += 1
                    print(f"  Added: {name}")
            except (OSError, PermissionError) as e:
                print(f"  Skipped {name}: {e}")
    print(f"Ingestion complete. Added {added} new file(s).")
    return added
```

**Step 4: Run tests**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_ingestor.py -v
```

Expected: `4 passed`

**Step 5: Commit**

```
git add core/ingestor.py tests/test_ingestor.py
git commit -m "feat: core ingestor with SHA256 dedup"
```

---

## Task 4: File Router (`core/router.py`)

**Files:**
- Create: `E:\DocVault\core\router.py`
- Create: `E:\DocVault\tests\test_router.py`

**Step 1: Write the failing tests**

```python
# tests/test_router.py
from core.router import get_extractors, UNKNOWN


def test_pdf_routes_to_text_and_image():
    extractors = get_extractors('pdf')
    names = [e.__name__ for e in extractors]
    assert 'text_extractor' in names
    assert 'image_extractor' in names


def test_docx_routes_to_word():
    extractors = get_extractors('docx')
    names = [e.__name__ for e in extractors]
    assert 'word_extractor' in names


def test_txt_routes_to_plaintext():
    extractors = get_extractors('txt')
    names = [e.__name__ for e in extractors]
    assert 'plaintext_extractor' in names


def test_wav_routes_to_metadata_and_transcriber():
    extractors = get_extractors('wav')
    names = [e.__name__ for e in extractors]
    assert 'metadata_extractor' in names
    assert 'transcriber' in names


def test_mp4_routes_to_video():
    extractors = get_extractors('mp4')
    names = [e.__name__ for e in extractors]
    assert 'video_extractor' in names


def test_unknown_extension_returns_unknown():
    extractors = get_extractors('xyz123')
    assert extractors == [UNKNOWN]


def test_jpg_routes_to_ocr():
    extractors = get_extractors('jpg')
    names = [e.__name__ for e in extractors]
    assert 'ocr_extractor' in names
```

**Step 2: Run to confirm failure**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_router.py -v
```

**Step 3: Create `E:\DocVault\core\router.py`**

```python
from extractors import (
    text_extractor,
    image_extractor,
    word_extractor,
    excel_extractor,
    pptx_extractor,
    plaintext_extractor,
    ocr_extractor,
    transcriber,
    video_extractor,
    metadata_extractor,
    unknown_extractor,
)

UNKNOWN = unknown_extractor

ROUTES = {
    # Documents
    'pdf':  [text_extractor, image_extractor],
    'docx': [word_extractor],
    'doc':  [word_extractor],
    'xlsx': [excel_extractor],
    'xls':  [excel_extractor],
    'pptx': [pptx_extractor],
    'ppt':  [pptx_extractor],
    # Plain text / code
    'txt':  [plaintext_extractor],
    'md':   [plaintext_extractor],
    'csv':  [plaintext_extractor],
    'json': [plaintext_extractor],
    'py':   [plaintext_extractor],
    'js':   [plaintext_extractor],
    'ts':   [plaintext_extractor],
    'html': [plaintext_extractor],
    'xml':  [plaintext_extractor],
    'yaml': [plaintext_extractor],
    'toml': [plaintext_extractor],
    'log':  [plaintext_extractor],
    # Images
    'jpg':  [ocr_extractor],
    'jpeg': [ocr_extractor],
    'png':  [ocr_extractor],
    'tiff': [ocr_extractor],
    'tif':  [ocr_extractor],
    'bmp':  [ocr_extractor],
    'webp': [ocr_extractor],
    # Audio
    'mp3':  [metadata_extractor, transcriber],
    'wav':  [metadata_extractor, transcriber],
    'm4a':  [metadata_extractor, transcriber],
    'flac': [metadata_extractor, transcriber],
    'ogg':  [metadata_extractor, transcriber],
    # Video
    'mp4':  [metadata_extractor, video_extractor],
    'mov':  [metadata_extractor, video_extractor],
    'mkv':  [metadata_extractor, video_extractor],
    'avi':  [metadata_extractor, video_extractor],
    'webm': [metadata_extractor, video_extractor],
}


def get_extractors(file_type: str) -> list:
    """Return the ordered list of extractors for a given file extension."""
    return ROUTES.get(file_type.lower(), [UNKNOWN])
```

**Step 4: Create all extractor stubs** so the router can import them.

Create each of these as a minimal stub (full implementation in Tasks 5–14):

`E:\DocVault\extractors\word_extractor.py`:
```python
def extract(file_path): return None, "word_extractor not yet implemented"
```

`E:\DocVault\extractors\excel_extractor.py`:
```python
def extract(file_path): return None, "excel_extractor not yet implemented"
```

`E:\DocVault\extractors\pptx_extractor.py`:
```python
def extract(file_path): return None, "pptx_extractor not yet implemented"
```

`E:\DocVault\extractors\plaintext_extractor.py`:
```python
def extract(file_path): return None, "plaintext_extractor not yet implemented"
```

`E:\DocVault\extractors\ocr_extractor.py`:
```python
def extract(file_path): return None, "ocr_extractor not yet implemented"
```

`E:\DocVault\extractors\video_extractor.py`:
```python
def extract(file_path): return None, "video_extractor not yet implemented"
```

`E:\DocVault\extractors\unknown_extractor.py`:
```python
import os
def extract(file_path):
    ext = os.path.splitext(file_path)[1]
    return None, f"UNKNOWN file type: {ext} — no extractor registered"
```

Also copy from `E:\Epstein\extractors\`:
- `text_extractor.py`
- `image_extractor.py`
- `transcriber.py`
- `metadata_extractor.py`

**Step 5: Run tests**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_router.py -v
```

Expected: `7 passed`

**Step 6: Commit**

```
git add core/router.py extractors/ tests/test_router.py
git commit -m "feat: file router with extension-to-extractor mapping"
```

---

## Task 5: Plain Text Extractor

**Files:**
- Modify: `E:\DocVault\extractors\plaintext_extractor.py`
- Create: `E:\DocVault\tests\test_plaintext_extractor.py`

**Step 1: Write the failing tests**

```python
# tests/test_plaintext_extractor.py
import os, tempfile, pytest
from extractors import plaintext_extractor


def test_extracts_txt_file(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("Hello world", encoding='utf-8')
    text, error = plaintext_extractor.extract(str(f))
    assert error is None
    assert "Hello world" in text


def test_extracts_markdown(tmp_path):
    f = tmp_path / "readme.md"
    f.write_text("# Title\n\nSome content.", encoding='utf-8')
    text, error = plaintext_extractor.extract(str(f))
    assert error is None
    assert "Title" in text


def test_returns_error_on_missing_file():
    text, error = plaintext_extractor.extract("/nonexistent/file.txt")
    assert text is None
    assert error is not None


def test_handles_binary_looking_file(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("col1,col2\n1,2\n3,4", encoding='utf-8')
    text, error = plaintext_extractor.extract(str(f))
    assert error is None
    assert "col1" in text
```

**Step 2: Run to confirm failure**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_plaintext_extractor.py -v
```

**Step 3: Implement `extractors/plaintext_extractor.py`**

```python
import os


# Extensions we treat as UTF-8 text
TEXT_EXTENSIONS = {
    'txt', 'md', 'markdown', 'csv', 'json', 'yaml', 'yml',
    'toml', 'xml', 'html', 'htm', 'log', 'py', 'js', 'ts',
    'jsx', 'tsx', 'css', 'sh', 'bat', 'ps1', 'ini', 'cfg',
    'rst', 'tex', 'sql', 'r', 'rb', 'java', 'c', 'cpp', 'h',
}

ENCODINGS = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']


def extract(file_path: str) -> tuple:
    """
    Reads a plain-text file and returns its content.
    Tries multiple encodings before giving up.
    """
    print(f"  [text/plain] Reading: {os.path.basename(file_path)}")
    for encoding in ENCODINGS:
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                content = f.read()
            return content, None
        except UnicodeDecodeError:
            continue
        except (OSError, FileNotFoundError) as e:
            return None, f"Could not read file: {e}"
    return None, f"Could not decode file with any supported encoding"
```

**Step 4: Run tests**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_plaintext_extractor.py -v
```

Expected: `4 passed`

**Step 5: Commit**

```
git add extractors/plaintext_extractor.py tests/test_plaintext_extractor.py
git commit -m "feat: plain text extractor with multi-encoding support"
```

---

## Task 6: Word / Excel / PowerPoint Extractors

**Files:**
- Modify: `E:\DocVault\extractors\word_extractor.py`
- Modify: `E:\DocVault\extractors\excel_extractor.py`
- Modify: `E:\DocVault\extractors\pptx_extractor.py`
- Create: `E:\DocVault\tests\test_office_extractors.py`

**Step 1: Write the failing tests**

```python
# tests/test_office_extractors.py
import pytest
from unittest.mock import patch, MagicMock
from extractors import word_extractor, excel_extractor, pptx_extractor


class TestWordExtractor:
    @patch('extractors.word_extractor.Document')
    def test_extracts_paragraphs(self, mock_doc_class):
        mock_para = MagicMock()
        mock_para.text = "Hello from Word"
        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para]
        mock_doc_class.return_value = mock_doc

        text, error = word_extractor.extract("fake.docx")
        assert error is None
        assert "Hello from Word" in text

    @patch('extractors.word_extractor.Document')
    def test_returns_error_on_exception(self, mock_doc_class):
        mock_doc_class.side_effect = Exception("Corrupt file")
        text, error = word_extractor.extract("fake.docx")
        assert text is None
        assert "Corrupt file" in error


class TestExcelExtractor:
    @patch('extractors.excel_extractor.load_workbook')
    def test_extracts_cell_values(self, mock_load):
        mock_ws = MagicMock()
        mock_ws.title = "Sheet1"
        mock_ws.iter_rows.return_value = [
            [MagicMock(value="Name"), MagicMock(value="Age")],
            [MagicMock(value="Alice"), MagicMock(value=30)],
        ]
        mock_wb = MagicMock()
        mock_wb.sheetnames = ["Sheet1"]
        mock_wb.__getitem__ = lambda self, k: mock_ws
        mock_load.return_value = mock_wb

        text, error = excel_extractor.extract("fake.xlsx")
        assert error is None
        assert "Name" in text
        assert "Alice" in text

    @patch('extractors.excel_extractor.load_workbook')
    def test_returns_error_on_exception(self, mock_load):
        mock_load.side_effect = Exception("Bad file")
        text, error = excel_extractor.extract("fake.xlsx")
        assert text is None
        assert error is not None


class TestPptxExtractor:
    @patch('extractors.pptx_extractor.Presentation')
    def test_extracts_slide_text(self, mock_prs_class):
        mock_shape = MagicMock()
        mock_shape.has_text_frame = True
        mock_shape.text_frame.text = "Slide title"
        mock_slide = MagicMock()
        mock_slide.shapes = [mock_shape]
        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]
        mock_prs_class.return_value = mock_prs

        text, error = pptx_extractor.extract("fake.pptx")
        assert error is None
        assert "Slide title" in text

    @patch('extractors.pptx_extractor.Presentation')
    def test_returns_error_on_exception(self, mock_prs_class):
        mock_prs_class.side_effect = Exception("Bad pptx")
        text, error = pptx_extractor.extract("fake.pptx")
        assert text is None
        assert error is not None
```

**Step 2: Run to confirm failure**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_office_extractors.py -v
```

**Step 3: Implement `extractors/word_extractor.py`**

```python
import os
from docx import Document


def extract(file_path: str) -> tuple:
    print(f"  [word] Extracting: {os.path.basename(file_path)}")
    try:
        doc = Document(file_path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        if not paragraphs:
            return None, "No text found in document"
        return "\n\n".join(paragraphs), None
    except Exception as e:
        return None, f"Failed to extract Word document: {e}"
```

**Step 4: Implement `extractors/excel_extractor.py`**

```python
import os
from openpyxl import load_workbook


def extract(file_path: str) -> tuple:
    print(f"  [excel] Extracting: {os.path.basename(file_path)}")
    try:
        wb = load_workbook(file_path, read_only=True, data_only=True)
        sections = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = []
            for row in ws.iter_rows():
                cells = [str(c.value) for c in row if c.value is not None]
                if cells:
                    rows.append("\t".join(cells))
            if rows:
                sections.append(f"[Sheet: {sheet_name}]\n" + "\n".join(rows))
        if not sections:
            return None, "No content found in spreadsheet"
        return "\n\n".join(sections), None
    except Exception as e:
        return None, f"Failed to extract Excel file: {e}"
```

**Step 5: Implement `extractors/pptx_extractor.py`**

```python
import os
from pptx import Presentation


def extract(file_path: str) -> tuple:
    print(f"  [pptx] Extracting: {os.path.basename(file_path)}")
    try:
        prs = Presentation(file_path)
        slides = []
        for i, slide in enumerate(prs.slides, start=1):
            texts = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    t = shape.text_frame.text.strip()
                    if t:
                        texts.append(t)
            if texts:
                slides.append(f"[Slide {i}]\n" + "\n".join(texts))
        if not slides:
            return None, "No text found in presentation"
        return "\n\n".join(slides), None
    except Exception as e:
        return None, f"Failed to extract PowerPoint file: {e}"
```

**Step 6: Run tests**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_office_extractors.py -v
```

Expected: `6 passed`

**Step 7: Commit**

```
git add extractors/word_extractor.py extractors/excel_extractor.py extractors/pptx_extractor.py tests/test_office_extractors.py
git commit -m "feat: Word, Excel, PowerPoint extractors"
```

---

## Task 7: OCR Extractor

**Files:**
- Modify: `E:\DocVault\extractors\ocr_extractor.py`
- Create: `E:\DocVault\tests\test_ocr_extractor.py`

**Prerequisite:** Tesseract must be installed.
Download from: https://github.com/UB-Mannheim/tesseract/wiki
Default install path: `C:\Program Files\Tesseract-OCR\tesseract.exe`

**Step 1: Write the failing tests**

```python
# tests/test_ocr_extractor.py
from unittest.mock import patch, MagicMock
from extractors import ocr_extractor


@patch('extractors.ocr_extractor.pytesseract.image_to_string')
@patch('extractors.ocr_extractor.Image.open')
def test_extract_returns_text(mock_open, mock_ocr):
    mock_open.return_value = MagicMock()
    mock_ocr.return_value = "Extracted OCR text"

    text, error = ocr_extractor.extract("fake.png")
    assert error is None
    assert "Extracted OCR text" in text


@patch('extractors.ocr_extractor.Image.open')
def test_extract_returns_error_on_failure(mock_open):
    mock_open.side_effect = Exception("Cannot open image")

    text, error = ocr_extractor.extract("fake.png")
    assert text is None
    assert "Cannot open image" in error


@patch('extractors.ocr_extractor.pytesseract.image_to_string')
@patch('extractors.ocr_extractor.Image.open')
def test_extract_returns_error_when_no_text(mock_open, mock_ocr):
    mock_open.return_value = MagicMock()
    mock_ocr.return_value = "   \n  "  # whitespace only

    text, error = ocr_extractor.extract("fake.png")
    assert text is None
    assert error is not None
```

**Step 2: Run to confirm failure**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_ocr_extractor.py -v
```

**Step 3: Implement `extractors/ocr_extractor.py`**

```python
import os
import pytesseract
from PIL import Image

# Tesseract path for Windows — adjust if installed elsewhere
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'


def extract(file_path: str) -> tuple:
    """
    Runs Tesseract OCR on an image file and returns extracted text.
    """
    print(f"  [ocr] Processing: {os.path.basename(file_path)}")
    try:
        image = Image.open(file_path)
        text = pytesseract.image_to_string(image, lang='eng')
        text = text.strip()
        if not text:
            return None, "No text detected in image"
        return text, None
    except Exception as e:
        return None, f"OCR failed: {e}"
```

**Step 4: Run tests**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_ocr_extractor.py -v
```

Expected: `3 passed`

**Step 5: Commit**

```
git add extractors/ocr_extractor.py tests/test_ocr_extractor.py
git commit -m "feat: OCR extractor via Tesseract"
```

---

## Task 8: Video Extractor

**Files:**
- Modify: `E:\DocVault\extractors\video_extractor.py`
- Create: `E:\DocVault\tests\test_video_extractor.py`

Video extraction: use ffmpeg to strip audio to a temp WAV file, then pass to `transcriber.extract()`.

**Step 1: Write the failing tests**

```python
# tests/test_video_extractor.py
from unittest.mock import patch, MagicMock
from extractors import video_extractor


@patch('extractors.video_extractor.transcriber.extract')
@patch('extractors.video_extractor._extract_audio')
def test_extract_transcribes_audio(mock_audio, mock_transcribe):
    mock_audio.return_value = ('/tmp/audio.wav', None)
    mock_transcribe.return_value = ('Video transcript text.', None)

    text, error = video_extractor.extract('fake.mp4')
    assert error is None
    assert text == 'Video transcript text.'
    mock_audio.assert_called_once_with('fake.mp4')


@patch('extractors.video_extractor._extract_audio')
def test_extract_returns_error_on_audio_failure(mock_audio):
    mock_audio.return_value = (None, 'ffmpeg error')

    text, error = video_extractor.extract('fake.mp4')
    assert text is None
    assert 'ffmpeg error' in error


@patch('extractors.video_extractor.transcriber.extract')
@patch('extractors.video_extractor._extract_audio')
def test_extract_returns_error_on_transcription_failure(mock_audio, mock_transcribe):
    mock_audio.return_value = ('/tmp/audio.wav', None)
    mock_transcribe.return_value = (None, 'Whisper failed')

    text, error = video_extractor.extract('fake.mp4')
    assert text is None
    assert 'Whisper failed' in error
```

**Step 2: Run to confirm failure**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_video_extractor.py -v
```

**Step 3: Implement `extractors/video_extractor.py`**

```python
import os
import tempfile
import ffmpeg
from extractors import transcriber


def _extract_audio(video_path: str) -> tuple:
    """Extract audio from video to a temporary WAV file."""
    try:
        tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        tmp.close()
        (
            ffmpeg
            .input(video_path)
            .output(tmp.name, acodec='pcm_s16le', ac=1, ar='16000', y=None)
            .run(quiet=True, overwrite_output=True)
        )
        return tmp.name, None
    except ffmpeg.Error as e:
        return None, f"Audio extraction failed: {e.stderr.decode('utf8', errors='replace')}"
    except Exception as e:
        return None, f"Unexpected error extracting audio: {e}"


def extract(file_path: str) -> tuple:
    """
    Extracts audio from a video file and transcribes it via Whisper.
    """
    print(f"  [video] Processing: {os.path.basename(file_path)}")
    audio_path, err = _extract_audio(file_path)
    if err:
        return None, err
    try:
        text, err = transcriber.extract(audio_path)
        return text, err
    finally:
        if audio_path and os.path.exists(audio_path):
            os.unlink(audio_path)
```

**Step 4: Run tests**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_video_extractor.py -v
```

Expected: `3 passed`

**Step 5: Commit**

```
git add extractors/video_extractor.py tests/test_video_extractor.py
git commit -m "feat: video extractor (ffmpeg audio strip + Whisper)"
```

---

## Task 9: Extraction Worker (`workers/extraction_worker.py`)

**Files:**
- Create: `E:\DocVault\workers\extraction_worker.py`
- Create: `E:\DocVault\tests\test_extraction_worker.py`

**Step 1: Write the failing tests**

```python
# tests/test_extraction_worker.py
import pytest
from unittest.mock import patch, MagicMock
from workers import extraction_worker


@patch('workers.extraction_worker.manager.complete_extraction')
@patch('workers.extraction_worker.manager.insert_extracted_image')
@patch('workers.extraction_worker.router.get_extractors')
def test_process_task_success(mock_router, mock_insert_img, mock_complete):
    mock_extractor = MagicMock()
    mock_extractor.__name__ = 'text_extractor'
    mock_extractor.extract.return_value = ('Extracted text', None)
    mock_router.return_value = [mock_extractor]

    task = {'file_hash': 'abc', 'file_path': '/docs/test.pdf', 'file_type': 'pdf'}
    extraction_worker.process_task('test.db', task)

    mock_complete.assert_called_once()
    call_kwargs = mock_complete.call_args[1]
    assert call_kwargs['status'] == 'EXTRACTED'
    assert call_kwargs['text'] == 'Extracted text'


@patch('workers.extraction_worker.manager.complete_extraction')
@patch('workers.extraction_worker.router.get_extractors')
def test_process_task_error(mock_router, mock_complete):
    mock_extractor = MagicMock()
    mock_extractor.__name__ = 'text_extractor'
    mock_extractor.extract.return_value = (None, 'File not found')
    mock_router.return_value = [mock_extractor]

    task = {'file_hash': 'abc', 'file_path': '/docs/missing.pdf', 'file_type': 'pdf'}
    extraction_worker.process_task('test.db', task)

    call_kwargs = mock_complete.call_args[1]
    assert call_kwargs['status'] == 'ERROR'
    assert 'File not found' in call_kwargs['error']


@patch('workers.extraction_worker.manager.complete_extraction')
@patch('workers.extraction_worker.router.get_extractors')
def test_unknown_file_type_flagged(mock_router, mock_complete):
    from extractors import unknown_extractor
    mock_router.return_value = [unknown_extractor]

    task = {'file_hash': 'abc', 'file_path': '/docs/weird.xyz', 'file_type': 'xyz'}
    extraction_worker.process_task('test.db', task)

    call_kwargs = mock_complete.call_args[1]
    assert call_kwargs['status'] == 'UNKNOWN'
```

**Step 2: Run to confirm failure**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_extraction_worker.py -v
```

**Step 3: Create `E:\DocVault\workers\extraction_worker.py`**

```python
import os
import socket
import time
from core import manager, router
from extractors import image_extractor, unknown_extractor


def process_task(db_path, task):
    """
    Run all registered extractors for a task, store results.
    """
    file_hash = task['file_hash']
    file_path = task['file_path']
    file_type = task['file_type'] or ''

    print(f"Processing [{file_type}]: {os.path.basename(file_path)}")

    extractors = router.get_extractors(file_type)

    # Unknown file type — flag and move on
    if extractors == [unknown_extractor]:
        _, msg = unknown_extractor.extract(file_path)
        manager.complete_extraction(db_path, file_hash, status='UNKNOWN', error=msg)
        print(f"  Flagged as UNKNOWN: {file_type}")
        return

    errors = []
    combined_text = []
    combined_metadata = {}

    for extractor in extractors:
        result, err = extractor.extract(file_path)

        if err:
            errors.append(f"[{extractor.__name__}] {err}")
            continue

        # Image extractor returns a list of dicts — store each in extracted_images
        if extractor is image_extractor and isinstance(result, list):
            for img_meta in result:
                manager.insert_extracted_image(db_path, file_hash, img_meta)

        # Text-returning extractors
        elif isinstance(result, str) and result:
            combined_text.append(result)

        # Metadata-returning extractors
        elif isinstance(result, dict):
            combined_metadata.update(result)

    final_text = "\n\n".join(combined_text) if combined_text else None
    final_status = 'ERROR' if errors else 'EXTRACTED'

    manager.complete_extraction(
        db_path,
        file_hash,
        status=final_status,
        text=final_text,
        metadata=combined_metadata if combined_metadata else None,
        error=' | '.join(errors) if errors else None,
    )


def run(db_path, worker_id=None):
    """Main loop — claim and process PENDING tasks until paused or empty."""
    if worker_id is None:
        worker_id = f"extract-{socket.gethostname()}-{os.getpid()}"
    print(f"Extraction worker starting. ID: {worker_id}")

    while True:
        if manager.get_pause_state(db_path):
            print("Paused. Sleeping 10s...")
            time.sleep(10)
            continue

        task = manager.claim_pending_task(db_path, worker_id)
        if task:
            process_task(db_path, task)
        else:
            print("No pending tasks. Sleeping 10s...")
            time.sleep(10)
```

**Step 4: Run tests**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_extraction_worker.py -v
```

Expected: `3 passed`

**Step 5: Run full suite**

```
cd E:\DocVault && venv\Scripts\python -m pytest -v
```

Expected: all pass.

**Step 6: Commit**

```
git add workers/extraction_worker.py tests/test_extraction_worker.py
git commit -m "feat: extraction worker with router integration and unknown-type flagging"
```

---

## Task 10: Embeddings — Chunker, Embedder, Vector Store

**Files:**
- Create: `E:\DocVault\embeddings\chunker.py`
- Create: `E:\DocVault\embeddings\embedder.py`
- Create: `E:\DocVault\embeddings\vector_store.py`
- Create: `E:\DocVault\tests\test_embeddings.py`

**Step 1: Write the failing tests**

```python
# tests/test_embeddings.py
import pytest
from unittest.mock import patch, MagicMock
from embeddings import chunker, embedder, vector_store


class TestChunker:
    def test_short_text_is_single_chunk(self):
        text = "Hello world"
        chunks = chunker.chunk(text)
        assert len(chunks) == 1
        assert chunks[0] == "Hello world"

    def test_long_text_is_split(self):
        text = " ".join(["word"] * 600)  # 600 words
        chunks = chunker.chunk(text, max_tokens=500, overlap=50)
        assert len(chunks) > 1

    def test_overlap_exists_between_chunks(self):
        text = " ".join([f"word{i}" for i in range(200)])
        chunks = chunker.chunk(text, max_tokens=100, overlap=20)
        # Last words of chunk N should appear at start of chunk N+1
        if len(chunks) > 1:
            last_words_c0 = set(chunks[0].split()[-20:])
            first_words_c1 = set(chunks[1].split()[:20])
            assert len(last_words_c0 & first_words_c1) > 0

    def test_empty_text_returns_empty_list(self):
        assert chunker.chunk("") == []
        assert chunker.chunk("   ") == []


class TestEmbedder:
    @patch('embeddings.embedder.ollama.embeddings')
    def test_embed_returns_vector(self, mock_embed):
        mock_embed.return_value = {'embedding': [0.1, 0.2, 0.3]}
        vec = embedder.embed("hello world")
        assert vec == [0.1, 0.2, 0.3]

    @patch('embeddings.embedder.ollama.embeddings')
    def test_embed_returns_none_on_error(self, mock_embed):
        mock_embed.side_effect = Exception("Ollama unavailable")
        vec = embedder.embed("hello world")
        assert vec is None


class TestVectorStore:
    @patch('embeddings.vector_store.QdrantClient')
    def test_upsert_points(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        vs = vector_store.VectorStore('localhost', 6333, 'test')
        vs.upsert('hash1', 0, [0.1]*768, {'file_path': '/test.pdf'})
        mock_client.upsert.assert_called_once()

    @patch('embeddings.vector_store.QdrantClient')
    def test_search_returns_results(self, mock_client_class):
        mock_result = MagicMock()
        mock_result.payload = {'file_hash': 'abc', 'chunk_text': 'hello'}
        mock_result.score = 0.95
        mock_client = MagicMock()
        mock_client.search.return_value = [mock_result]
        mock_client_class.return_value = mock_client

        vs = vector_store.VectorStore('localhost', 6333, 'test')
        results = vs.search([0.1]*768, top_k=5)
        assert len(results) == 1
        assert results[0]['score'] == 0.95
```

**Step 2: Run to confirm failure**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_embeddings.py -v
```

**Step 3: Implement `embeddings/chunker.py`**

```python
def chunk(text: str, max_tokens: int = 500, overlap: int = 50) -> list[str]:
    """
    Split text into overlapping word-based chunks.
    Uses words as a proxy for tokens (1 token ≈ 0.75 words — close enough).
    """
    text = text.strip()
    if not text:
        return []
    words = text.split()
    if len(words) <= max_tokens:
        return [text]
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + max_tokens, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start += max_tokens - overlap
    return chunks
```

**Step 4: Implement `embeddings/embedder.py`**

```python
import ollama
import configparser
import os

_cfg = configparser.ConfigParser()
_cfg.read(os.path.join(os.path.dirname(__file__), '..', 'config.ini'))
EMBED_MODEL = _cfg.get('ollama', 'embed_model', fallback='nomic-embed-text')
OLLAMA_HOST = _cfg.get('ollama', 'host', fallback='http://localhost:11434')


def embed(text: str) -> list[float] | None:
    """Generate an embedding vector for the given text using Ollama."""
    try:
        response = ollama.embeddings(model=EMBED_MODEL, prompt=text)
        return response['embedding']
    except Exception as e:
        print(f"  [embed] Error: {e}")
        return None
```

**Step 5: Implement `embeddings/vector_store.py`**

```python
from qdrant_client import QdrantClient
from qdrant_client.http import models
import uuid


class VectorStore:
    def __init__(self, host: str, port: int, collection: str, vector_size: int = 768):
        self.client = QdrantClient(host=host, port=port)
        self.collection = collection
        self._ensure_collection(vector_size)

    def _ensure_collection(self, vector_size):
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=vector_size,
                    distance=models.Distance.COSINE,
                    on_disk=True,
                )
            )

    def upsert(self, file_hash: str, chunk_index: int,
               vector: list[float], payload: dict):
        point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS,
                                   f"{file_hash}:{chunk_index}"))
        self.client.upsert(
            collection_name=self.collection,
            points=[models.PointStruct(
                id=point_id,
                vector=vector,
                payload={**payload, 'file_hash': file_hash,
                         'chunk_index': chunk_index}
            )]
        )

    def search(self, query_vector: list[float],
               top_k: int = 5) -> list[dict]:
        results = self.client.search(
            collection_name=self.collection,
            query_vector=query_vector,
            limit=top_k,
        )
        return [{'score': r.score, **r.payload} for r in results]

    def delete_by_hash(self, file_hash: str):
        self.client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(
                        key='file_hash',
                        match=models.MatchValue(value=file_hash)
                    )]
                )
            )
        )
```

**Step 6: Run tests**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_embeddings.py -v
```

Expected: `8 passed`

**Step 7: Commit**

```
git add embeddings/ tests/test_embeddings.py
git commit -m "feat: chunker, embedder, and Qdrant vector store"
```

---

## Task 11: Embedding Worker (`workers/embedding_worker.py`)

**Files:**
- Create: `E:\DocVault\workers\embedding_worker.py`
- Create: `E:\DocVault\tests\test_embedding_worker.py`

**Step 1: Write the failing tests**

```python
# tests/test_embedding_worker.py
from unittest.mock import patch, MagicMock, call
from workers import embedding_worker


@patch('workers.embedding_worker.manager.complete_extraction')
@patch('workers.embedding_worker.VectorStore')
@patch('workers.embedding_worker.embedder.embed')
@patch('workers.embedding_worker.chunker.chunk')
def test_process_task_embeds_all_chunks(mock_chunk, mock_embed,
                                         mock_vs_class, mock_complete):
    mock_chunk.return_value = ['chunk one', 'chunk two']
    mock_embed.return_value = [0.1] * 768
    mock_vs = MagicMock()
    mock_vs_class.return_value = mock_vs

    task = {
        'file_hash': 'abc', 'file_path': '/test.pdf',
        'file_type': 'pdf', 'extracted_text': 'chunk one chunk two'
    }
    embedding_worker.process_task('test.db', task, mock_vs)

    assert mock_embed.call_count == 2
    assert mock_vs.upsert.call_count == 2
    mock_complete.assert_called_once_with(
        'test.db', 'abc', status='COMPLETED'
    )


@patch('workers.embedding_worker.manager.complete_extraction')
@patch('workers.embedding_worker.embedder.embed')
@patch('workers.embedding_worker.chunker.chunk')
def test_process_task_handles_empty_text(mock_chunk, mock_embed, mock_complete):
    mock_chunk.return_value = []
    vs = MagicMock()

    task = {
        'file_hash': 'abc', 'file_path': '/test.pdf',
        'file_type': 'pdf', 'extracted_text': ''
    }
    embedding_worker.process_task('test.db', task, vs)

    mock_embed.assert_not_called()
    mock_complete.assert_called_once_with(
        'test.db', 'abc', status='COMPLETED'
    )
```

**Step 2: Run to confirm failure**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_embedding_worker.py -v
```

**Step 3: Create `E:\DocVault\workers\embedding_worker.py`**

```python
import os
import socket
import time
import configparser
from core import manager
from embeddings import chunker, embedder
from embeddings.vector_store import VectorStore


def _load_vector_store():
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(os.path.dirname(__file__), '..', 'config.ini'))
    return VectorStore(
        host=cfg.get('qdrant', 'host', fallback='localhost'),
        port=cfg.getint('qdrant', 'port', fallback=6333),
        collection=cfg.get('qdrant', 'collection', fallback='docvault'),
    )


def process_task(db_path, task, vs):
    file_hash = task['file_hash']
    file_path = task['file_path']
    text = task.get('extracted_text') or ''

    print(f"Embedding: {os.path.basename(file_path)}")

    chunks = chunker.chunk(text)
    for i, chunk_text in enumerate(chunks):
        vector = embedder.embed(chunk_text)
        if vector:
            vs.upsert(
                file_hash=file_hash,
                chunk_index=i,
                vector=vector,
                payload={
                    'file_path': file_path,
                    'chunk_text': chunk_text,
                    'chunk_index': i,
                }
            )

    manager.complete_extraction(db_path, file_hash, status='COMPLETED')
    print(f"  Embedded {len(chunks)} chunk(s).")


def run(db_path, worker_id=None):
    if worker_id is None:
        worker_id = f"embed-{socket.gethostname()}-{os.getpid()}"
    print(f"Embedding worker starting. ID: {worker_id}")

    vs = _load_vector_store()

    while True:
        if manager.get_pause_state(db_path):
            print("Paused. Sleeping 10s...")
            time.sleep(10)
            continue

        task = manager.claim_extracted_task(db_path, worker_id)
        if task:
            process_task(db_path, task, vs)
        else:
            print("No extracted tasks. Sleeping 10s...")
            time.sleep(10)
```

**Step 4: Run tests**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_embedding_worker.py -v
```

Expected: `2 passed`

**Step 5: Commit**

```
git add workers/embedding_worker.py tests/test_embedding_worker.py
git commit -m "feat: embedding worker (chunk → embed → Qdrant)"
```

---

## Task 12: LLM Providers (`llm/`)

**Files:**
- Create: `E:\DocVault\llm\base.py`
- Create: `E:\DocVault\llm\ollama_provider.py`
- Create: `E:\DocVault\llm\claude_provider.py`
- Create: `E:\DocVault\tests\test_llm_providers.py`

**Step 1: Write the failing tests**

```python
# tests/test_llm_providers.py
from unittest.mock import patch, MagicMock
from llm.ollama_provider import OllamaProvider


class TestOllamaProvider:
    @patch('llm.ollama_provider.ollama.chat')
    def test_chat_returns_response(self, mock_chat):
        mock_chat.return_value = {
            'message': {'content': 'The answer is 42.'}
        }
        provider = OllamaProvider(model='llama3')
        result = provider.chat([{'role': 'user', 'content': 'What is the answer?'}])
        assert result == 'The answer is 42.'

    @patch('llm.ollama_provider.ollama.chat')
    def test_chat_returns_error_string_on_failure(self, mock_chat):
        mock_chat.side_effect = Exception("Connection refused")
        provider = OllamaProvider(model='llama3')
        result = provider.chat([{'role': 'user', 'content': 'hello'}])
        assert 'error' in result.lower() or 'Connection refused' in result
```

**Step 2: Run to confirm failure**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_llm_providers.py -v
```

**Step 3: Create `E:\DocVault\llm\base.py`**

```python
from abc import ABC, abstractmethod


class BaseLLMProvider(ABC):
    @abstractmethod
    def chat(self, messages: list[dict]) -> str:
        """Send messages to the LLM, return the response text."""
        ...

    def rag_query(self, question: str, chunks: list[str]) -> str:
        """Build a RAG prompt from retrieved chunks and query the LLM."""
        context = "\n\n---\n\n".join(chunks)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a helpful assistant. Answer the user's question "
                    "based only on the provided context. If the answer is not "
                    "in the context, say so.\n\nContext:\n" + context
                )
            },
            {"role": "user", "content": question}
        ]
        return self.chat(messages)
```

**Step 4: Create `E:\DocVault\llm\ollama_provider.py`**

```python
import ollama as _ollama
from llm.base import BaseLLMProvider


class OllamaProvider(BaseLLMProvider):
    def __init__(self, model: str = 'llama3', host: str = 'http://localhost:11434'):
        self.model = model
        self.host = host

    def chat(self, messages: list[dict]) -> str:
        try:
            response = _ollama.chat(model=self.model, messages=messages)
            return response['message']['content']
        except Exception as e:
            return f"LLM error: {e}"
```

**Step 5: Create `E:\DocVault\llm\claude_provider.py`** (stub — fill in API key when needed)

```python
import httpx
from llm.base import BaseLLMProvider


class ClaudeProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str = 'claude-sonnet-4-6'):
        self.api_key = api_key
        self.model = model

    def chat(self, messages: list[dict]) -> str:
        try:
            resp = httpx.post(
                'https://api.anthropic.com/v1/messages',
                headers={
                    'x-api-key': self.api_key,
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json',
                },
                json={
                    'model': self.model,
                    'max_tokens': 1024,
                    'messages': messages,
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()['content'][0]['text']
        except Exception as e:
            return f"Claude error: {e}"
```

**Step 6: Create `E:\DocVault\llm\factory.py`**

```python
import configparser, os


def get_provider():
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(os.path.dirname(__file__), '..', 'config.ini'))
    provider = cfg.get('llm', 'provider', fallback='ollama')

    if provider == 'ollama':
        from llm.ollama_provider import OllamaProvider
        return OllamaProvider(
            model=cfg.get('ollama', 'chat_model', fallback='llama3'),
            host=cfg.get('ollama', 'host', fallback='http://localhost:11434'),
        )
    if provider == 'claude':
        from llm.claude_provider import ClaudeProvider
        return ClaudeProvider(api_key=cfg.get('llm', 'api_key'))
    raise ValueError(f"Unknown LLM provider: {provider}")
```

**Step 7: Run tests**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_llm_providers.py -v
```

Expected: `2 passed`

**Step 8: Commit**

```
git add llm/ tests/test_llm_providers.py
git commit -m "feat: pluggable LLM providers (Ollama default, Claude stub)"
```

---

## Task 13: Search Layer (`search/`)

**Files:**
- Create: `E:\DocVault\search\fts.py`
- Create: `E:\DocVault\search\semantic.py`
- Create: `E:\DocVault\search\hybrid.py`
- Create: `E:\DocVault\tests\test_search.py`

**Step 1: Write the failing tests**

```python
# tests/test_search.py
import pytest
import tempfile
from core import manager
from search import fts, hybrid
from unittest.mock import patch, MagicMock


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    manager.insert_task(db_path, 'h1', '/docs/report.pdf', 'pdf')
    manager.insert_task(db_path, 'h2', '/docs/notes.txt', 'txt')
    manager.complete_extraction(db_path, 'h1',
        text='The quick brown fox jumps over the lazy dog', status='EXTRACTED')
    manager.complete_extraction(db_path, 'h2',
        text='Python is a great programming language', status='EXTRACTED')
    return db_path


def test_fts_finds_matching_document(db):
    results = fts.search(db, 'brown fox')
    assert len(results) >= 1
    assert any(r['file_hash'] == 'h1' for r in results)


def test_fts_returns_empty_for_no_match(db):
    results = fts.search(db, 'zzz_no_match_zzz')
    assert results == []


def test_fts_result_has_snippet(db):
    results = fts.search(db, 'programming')
    assert len(results) >= 1
    assert 'snippet' in results[0]


@patch('search.semantic.embedder.embed')
@patch('search.semantic.VectorStore')
def test_semantic_search_returns_results(mock_vs_class, mock_embed):
    mock_embed.return_value = [0.1] * 768
    mock_vs = MagicMock()
    mock_vs.search.return_value = [
        {'file_hash': 'h1', 'file_path': '/docs/report.pdf',
         'chunk_text': 'brown fox', 'score': 0.95}
    ]
    mock_vs_class.return_value = mock_vs

    from search import semantic
    results = semantic.search('brown fox', top_k=5)
    assert len(results) == 1
    assert results[0]['score'] == 0.95


def test_hybrid_merges_results(db):
    fts_results = [
        {'file_hash': 'h1', 'file_path': '/docs/a.pdf', 'snippet': 'foo', 'rank': -1.0}
    ]
    sem_results = [
        {'file_hash': 'h1', 'file_path': '/docs/a.pdf', 'chunk_text': 'foo', 'score': 0.9},
        {'file_hash': 'h2', 'file_path': '/docs/b.pdf', 'chunk_text': 'bar', 'score': 0.7},
    ]
    merged = hybrid.merge(fts_results, sem_results)
    hashes = [r['file_hash'] for r in merged]
    assert 'h1' in hashes
    assert 'h2' in hashes
    # h1 appears in both so should rank higher
    assert hashes.index('h1') < hashes.index('h2')
```

**Step 2: Run to confirm failure**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_search.py -v
```

**Step 3: Create `E:\DocVault\search\fts.py`**

```python
from core import manager


def search(db_path: str, query: str, limit: int = 20) -> list[dict]:
    """Full-text search via SQLite FTS5. Returns ranked results with snippets."""
    return manager.fts_search(db_path, query, limit)
```

**Step 4: Create `E:\DocVault\search\semantic.py`**

```python
import configparser, os
from embeddings import embedder
from embeddings.vector_store import VectorStore

_cfg = configparser.ConfigParser()
_cfg.read(os.path.join(os.path.dirname(__file__), '..', 'config.ini'))


def search(query: str, top_k: int = 5) -> list[dict]:
    """Semantic search via Qdrant. Returns top-k similar chunks."""
    vector = embedder.embed(query)
    if not vector:
        return []
    vs = VectorStore(
        host=_cfg.get('qdrant', 'host', fallback='localhost'),
        port=_cfg.getint('qdrant', 'port', fallback=6333),
        collection=_cfg.get('qdrant', 'collection', fallback='docvault'),
    )
    return vs.search(vector, top_k=top_k)
```

**Step 5: Create `E:\DocVault\search\hybrid.py`**

```python
def merge(fts_results: list[dict], semantic_results: list[dict],
          fts_weight: float = 0.4, sem_weight: float = 0.6) -> list[dict]:
    """
    Reciprocal Rank Fusion of FTS and semantic results.
    Returns deduplicated list sorted by combined score.
    """
    scores = {}
    seen = {}

    for rank, r in enumerate(fts_results, start=1):
        h = r['file_hash']
        scores[h] = scores.get(h, 0) + fts_weight * (1.0 / (60 + rank))
        if h not in seen:
            seen[h] = {**r, 'source': 'fts'}

    for rank, r in enumerate(semantic_results, start=1):
        h = r['file_hash']
        scores[h] = scores.get(h, 0) + sem_weight * (1.0 / (60 + rank))
        if h not in seen:
            seen[h] = {**r, 'source': 'semantic'}

    ranked = sorted(scores.keys(), key=lambda h: scores[h], reverse=True)
    return [{**seen[h], 'combined_score': scores[h]} for h in ranked]
```

**Step 6: Run tests**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_search.py -v
```

Expected: `5 passed`

**Step 7: Commit**

```
git add search/ tests/test_search.py
git commit -m "feat: FTS5, semantic, and hybrid search with RRF re-ranking"
```

---

## Task 14: FastAPI Backend (`api/`)

**Files:**
- Create: `E:\DocVault\api\main.py`
- Create: `E:\DocVault\api\routes\catalog.py`
- Create: `E:\DocVault\api\routes\search.py`
- Create: `E:\DocVault\api\routes\query.py`
- Create: `E:\DocVault\api\routes\workers.py`
- Create: `E:\DocVault\tests\test_api.py`

**Step 1: Write the failing tests**

```python
# tests/test_api.py
import pytest
import tempfile
from fastapi.testclient import TestClient
from unittest.mock import patch
from core import manager


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    manager.insert_task(db_path, 'h1', '/docs/test.pdf', 'pdf')
    manager.complete_extraction(db_path, 'h1',
        text='Hello world document', status='EXTRACTED')

    with patch('api.main.DB_PATH', db_path):
        from api.main import app
        return TestClient(app)


def test_get_stats(client):
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert 'total' in data
    assert data['total'] >= 1


def test_list_catalog(client):
    resp = client.get("/api/catalog")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    assert 'file_hash' in items[0]


def test_get_catalog_item(client):
    resp = client.get("/api/catalog/h1")
    assert resp.status_code == 200
    data = resp.json()
    assert data['file_hash'] == 'h1'
    assert data['extracted_text'] == 'Hello world document'


def test_fts_search(client):
    resp = client.get("/api/search?q=Hello&mode=fts")
    assert resp.status_code == 200
    results = resp.json()
    assert isinstance(results, list)


def test_worker_status(client):
    resp = client.get("/api/workers/status")
    assert resp.status_code == 200
    data = resp.json()
    assert 'paused' in data


def test_pause_worker(client):
    resp = client.post("/api/workers/pause")
    assert resp.status_code == 200
    resp2 = client.get("/api/workers/status")
    assert resp2.json()['paused'] is True
    client.post("/api/workers/resume")
```

**Step 2: Run to confirm failure**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_api.py -v
```

**Step 3: Create `E:\DocVault\api\routes\catalog.py`**

```python
from fastapi import APIRouter, HTTPException
from core import manager

router = APIRouter()


def get_db():
    from api.main import DB_PATH
    return DB_PATH


@router.get("/stats")
def get_stats():
    return manager.get_stats(get_db())


@router.get("/catalog")
def list_catalog(status: str = None, file_type: str = None,
                 limit: int = 100, offset: int = 0):
    return manager.list_tasks(get_db(), status=status,
                               file_type=file_type, limit=limit, offset=offset)


@router.get("/catalog/{file_hash}")
def get_catalog_item(file_hash: str):
    task = manager.get_task(get_db(), file_hash)
    if not task:
        raise HTTPException(status_code=404, detail="File not found")
    return task
```

**Step 4: Create `E:\DocVault\api\routes\search.py`**

```python
from fastapi import APIRouter, Query
from search import fts, semantic, hybrid

router = APIRouter()


def get_db():
    from api.main import DB_PATH
    return DB_PATH


@router.get("/search")
def search(q: str = Query(..., min_length=1),
           mode: str = Query('hybrid', regex='^(fts|semantic|hybrid)$'),
           limit: int = 20):
    if mode == 'fts':
        return fts.search(get_db(), q, limit)
    if mode == 'semantic':
        return semantic.search(q, top_k=limit)
    # hybrid
    fts_r = fts.search(get_db(), q, limit)
    sem_r = semantic.search(q, top_k=limit)
    return hybrid.merge(fts_r, sem_r)
```

**Step 5: Create `E:\DocVault\api\routes\query.py`**

```python
from fastapi import APIRouter
from pydantic import BaseModel
from search import semantic
from llm.factory import get_provider

router = APIRouter()


class QueryRequest(BaseModel):
    question: str
    top_k: int = 5
    provider: str = None  # override config default


@router.post("/query")
def rag_query(req: QueryRequest):
    results = semantic.search(req.question, top_k=req.top_k)
    chunks = [r.get('chunk_text', '') for r in results]
    sources = [{'file_path': r.get('file_path'), 'score': r.get('score')}
               for r in results]

    llm = get_provider()
    answer = llm.rag_query(req.question, chunks)

    return {'answer': answer, 'sources': sources}
```

**Step 6: Create `E:\DocVault\api\routes\workers.py`**

```python
from fastapi import APIRouter
from core import manager

router = APIRouter()


def get_db():
    from api.main import DB_PATH
    return DB_PATH


@router.get("/workers/status")
def worker_status():
    return {'paused': manager.get_pause_state(get_db())}


@router.post("/workers/pause")
def pause():
    manager.set_pause_state(get_db(), True)
    return {'paused': True}


@router.post("/workers/resume")
def resume():
    manager.set_pause_state(get_db(), False)
    return {'paused': False}
```

**Step 7: Create `E:\DocVault\api\main.py`**

```python
import configparser, os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from api.routes import catalog, search, query, workers

_cfg = configparser.ConfigParser()
_cfg.read(os.path.join(os.path.dirname(__file__), '..', 'config.ini'))
DB_PATH = _cfg.get('database', 'sqlite_path',
                    fallback=os.path.join(os.path.dirname(__file__),
                                          '..', 'docvault.db'))

app = FastAPI(title="DocVault", version="1.0.0")

app.include_router(catalog.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(query.router, prefix="/api")
app.include_router(workers.router, prefix="/api")

FRONTEND = os.path.join(os.path.dirname(__file__), '..', 'frontend')
app.mount("/static", StaticFiles(directory=os.path.join(FRONTEND, 'static')),
          name="static")


@app.get("/")
def root():
    return FileResponse(os.path.join(FRONTEND, 'index.html'))

@app.get("/catalog")
def catalog_page():
    return FileResponse(os.path.join(FRONTEND, 'catalog.html'))

@app.get("/search")
def search_page():
    return FileResponse(os.path.join(FRONTEND, 'search.html'))
```

**Step 8: Run tests**

```
cd E:\DocVault && venv\Scripts\python -m pytest tests/test_api.py -v
```

Expected: `6 passed`

**Step 9: Commit**

```
git add api/ tests/test_api.py
git commit -m "feat: FastAPI backend with catalog, search, RAG query, and worker control routes"
```

---

## Task 15: Web Frontend (Dashboard, Catalog, Search)

**Files:**
- Create: `E:\DocVault\frontend\static\styles.css`
- Create: `E:\DocVault\frontend\static\app.js`
- Create: `E:\DocVault\frontend\index.html`
- Create: `E:\DocVault\frontend\catalog.html`
- Create: `E:\DocVault\frontend\search.html`

No unit tests for the frontend. Manual verification in the browser.

**Step 1: Create `frontend/static/styles.css`**

```css
/* Tailwind via CDN is loaded in HTML. This file holds custom overrides only. */
.status-badge { @apply px-2 py-0.5 rounded text-xs font-semibold; }
.status-PENDING    { @apply bg-gray-200 text-gray-700; }
.status-EXTRACTED  { @apply bg-blue-100 text-blue-700; }
.status-COMPLETED  { @apply bg-green-100 text-green-700; }
.status-ERROR      { @apply bg-red-100 text-red-700; }
.status-UNKNOWN    { @apply bg-yellow-100 text-yellow-700; }
.status-PROCESSING { @apply bg-purple-100 text-purple-700; }
```

**Step 2: Create `frontend/static/app.js`**

```javascript
// Shared utilities for all pages

async function api(path, options = {}) {
  const resp = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!resp.ok) throw new Error(`API error ${resp.status}`);
  return resp.json();
}

function statusBadge(status) {
  return `<span class="status-badge status-${status}">${status}</span>`;
}

function formatPath(path) {
  return path ? path.split(/[/\\]/).pop() : '—';
}
```

**Step 3: Create `frontend/index.html`** (Dashboard)

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>DocVault — Dashboard</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="/static/app.js"></script>
</head>
<body class="bg-gray-50 min-h-screen">
  <nav class="bg-indigo-700 text-white px-6 py-3 flex gap-6 items-center shadow">
    <span class="font-bold text-lg">DocVault</span>
    <a href="/" class="hover:underline">Dashboard</a>
    <a href="/catalog" class="hover:underline">Catalog</a>
    <a href="/search" class="hover:underline">Search</a>
  </nav>

  <main class="max-w-5xl mx-auto p-6">
    <h1 class="text-2xl font-bold text-gray-800 mb-6">Dashboard</h1>

    <!-- Stats cards -->
    <div id="stats" class="grid grid-cols-4 gap-4 mb-8">
      <div class="bg-white rounded shadow p-4 text-center">
        <div id="stat-total" class="text-3xl font-bold text-indigo-600">—</div>
        <div class="text-sm text-gray-500 mt-1">Total Files</div>
      </div>
      <div class="bg-white rounded shadow p-4 text-center">
        <div id="stat-completed" class="text-3xl font-bold text-green-600">—</div>
        <div class="text-sm text-gray-500 mt-1">Completed</div>
      </div>
      <div class="bg-white rounded shadow p-4 text-center">
        <div id="stat-pending" class="text-3xl font-bold text-gray-500">—</div>
        <div class="text-sm text-gray-500 mt-1">Pending</div>
      </div>
      <div class="bg-white rounded shadow p-4 text-center">
        <div id="stat-error" class="text-3xl font-bold text-red-500">—</div>
        <div class="text-sm text-gray-500 mt-1">Errors</div>
      </div>
    </div>

    <!-- Worker controls -->
    <div class="bg-white rounded shadow p-6 mb-6">
      <h2 class="font-semibold text-gray-700 mb-3">Worker Controls</h2>
      <div class="flex gap-3 items-center">
        <span id="pause-status" class="text-sm text-gray-600">—</span>
        <button onclick="pauseWorkers()" class="bg-yellow-400 hover:bg-yellow-500 text-white px-4 py-1.5 rounded text-sm">Pause</button>
        <button onclick="resumeWorkers()" class="bg-green-500 hover:bg-green-600 text-white px-4 py-1.5 rounded text-sm">Resume</button>
      </div>
    </div>

    <!-- Unknown file types -->
    <div class="bg-white rounded shadow p-6">
      <h2 class="font-semibold text-gray-700 mb-3">Unknown File Types</h2>
      <div id="unknowns" class="text-sm text-gray-500">Loading...</div>
    </div>
  </main>

  <script>
    async function loadStats() {
      const s = await api('/stats');
      document.getElementById('stat-total').textContent = s.total;
      document.getElementById('stat-completed').textContent = s.completed;
      document.getElementById('stat-pending').textContent = s.pending;
      document.getElementById('stat-error').textContent = s.error;
    }

    async function loadWorkerStatus() {
      const s = await api('/workers/status');
      document.getElementById('pause-status').textContent =
        s.paused ? '⏸ Workers are PAUSED' : '▶ Workers are RUNNING';
    }

    async function loadUnknowns() {
      const items = await api('/catalog?status=UNKNOWN&limit=20');
      const el = document.getElementById('unknowns');
      if (!items.length) { el.textContent = 'None'; return; }
      el.innerHTML = items.map(i =>
        `<div class="py-1">${i.file_type || '?'} — ${formatPath(i.file_path)}</div>`
      ).join('');
    }

    async function pauseWorkers() {
      await api('/workers/pause', { method: 'POST' });
      loadWorkerStatus();
    }

    async function resumeWorkers() {
      await api('/workers/resume', { method: 'POST' });
      loadWorkerStatus();
    }

    loadStats();
    loadWorkerStatus();
    loadUnknowns();
    setInterval(() => { loadStats(); loadWorkerStatus(); }, 5000);
  </script>
</body>
</html>
```

**Step 4: Create `frontend/catalog.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>DocVault — Catalog</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="/static/app.js"></script>
</head>
<body class="bg-gray-50 min-h-screen">
  <nav class="bg-indigo-700 text-white px-6 py-3 flex gap-6 items-center shadow">
    <span class="font-bold text-lg">DocVault</span>
    <a href="/" class="hover:underline">Dashboard</a>
    <a href="/catalog" class="hover:underline font-semibold">Catalog</a>
    <a href="/search" class="hover:underline">Search</a>
  </nav>

  <main class="max-w-6xl mx-auto p-6">
    <h1 class="text-2xl font-bold text-gray-800 mb-4">Catalog</h1>

    <div class="flex gap-3 mb-4">
      <select id="filter-status" onchange="loadCatalog()"
        class="border rounded px-3 py-1.5 text-sm">
        <option value="">All statuses</option>
        <option>PENDING</option><option>EXTRACTED</option>
        <option>COMPLETED</option><option>ERROR</option><option>UNKNOWN</option>
      </select>
      <select id="filter-type" onchange="loadCatalog()"
        class="border rounded px-3 py-1.5 text-sm">
        <option value="">All types</option>
        <option>pdf</option><option>docx</option><option>txt</option>
        <option>wav</option><option>mp4</option>
      </select>
    </div>

    <div class="bg-white rounded shadow overflow-hidden">
      <table class="w-full text-sm">
        <thead class="bg-gray-100 text-gray-600 uppercase text-xs">
          <tr>
            <th class="p-3 text-left">File</th>
            <th class="p-3 text-left">Type</th>
            <th class="p-3 text-left">Status</th>
            <th class="p-3 text-left">Updated</th>
          </tr>
        </thead>
        <tbody id="catalog-body" class="divide-y divide-gray-100"></tbody>
      </table>
    </div>

    <!-- Detail panel -->
    <div id="detail" class="hidden mt-6 bg-white rounded shadow p-6">
      <h2 id="detail-title" class="font-semibold text-gray-700 mb-2"></h2>
      <pre id="detail-text"
        class="text-xs bg-gray-50 p-3 rounded max-h-64 overflow-y-auto whitespace-pre-wrap"></pre>
    </div>
  </main>

  <script>
    async function loadCatalog() {
      const status = document.getElementById('filter-status').value;
      const type = document.getElementById('filter-type').value;
      let url = '/catalog?limit=100';
      if (status) url += `&status=${status}`;
      if (type) url += `&file_type=${type}`;
      const items = await api(url);
      const body = document.getElementById('catalog-body');
      body.innerHTML = items.map(i => `
        <tr class="hover:bg-gray-50 cursor-pointer" onclick="showDetail('${i.file_hash}')">
          <td class="p-3 font-mono text-xs">${formatPath(i.file_path)}</td>
          <td class="p-3 text-gray-500">${i.file_type || '?'}</td>
          <td class="p-3">${statusBadge(i.status)}</td>
          <td class="p-3 text-gray-400">${i.last_update?.split('T')[0] || ''}</td>
        </tr>`).join('');
    }

    async function showDetail(hash) {
      const item = await api(`/catalog/${hash}`);
      document.getElementById('detail-title').textContent =
        formatPath(item.file_path) + ` [${item.file_type}]`;
      document.getElementById('detail-text').textContent =
        item.extracted_text || item.error_log || '(no content)';
      document.getElementById('detail').classList.remove('hidden');
    }

    loadCatalog();
  </script>
</body>
</html>
```

**Step 5: Create `frontend/search.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>DocVault — Search</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="/static/app.js"></script>
</head>
<body class="bg-gray-50 min-h-screen">
  <nav class="bg-indigo-700 text-white px-6 py-3 flex gap-6 items-center shadow">
    <span class="font-bold text-lg">DocVault</span>
    <a href="/" class="hover:underline">Dashboard</a>
    <a href="/catalog" class="hover:underline">Catalog</a>
    <a href="/search" class="hover:underline font-semibold">Search</a>
  </nav>

  <main class="max-w-4xl mx-auto p-6">
    <h1 class="text-2xl font-bold text-gray-800 mb-6">Search & Ask</h1>

    <!-- Search bar -->
    <div class="bg-white rounded shadow p-4 mb-6">
      <div class="flex gap-3 mb-3">
        <input id="search-input" type="text" placeholder="Search documents..."
          class="flex-1 border rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
          onkeydown="if(event.key==='Enter') doSearch()">
        <select id="search-mode" class="border rounded px-3 py-2 text-sm">
          <option value="hybrid">Hybrid</option>
          <option value="fts">Full-text</option>
          <option value="semantic">Semantic</option>
        </select>
        <button onclick="doSearch()"
          class="bg-indigo-600 hover:bg-indigo-700 text-white px-5 py-2 rounded text-sm">
          Search
        </button>
      </div>
      <div id="search-results" class="divide-y divide-gray-100"></div>
    </div>

    <!-- RAG query -->
    <div class="bg-white rounded shadow p-4">
      <h2 class="font-semibold text-gray-700 mb-3">Ask a Question</h2>
      <div class="flex gap-3 mb-3">
        <input id="query-input" type="text" placeholder="Ask anything about your documents..."
          class="flex-1 border rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
          onkeydown="if(event.key==='Enter') doQuery()">
        <button onclick="doQuery()"
          class="bg-green-600 hover:bg-green-700 text-white px-5 py-2 rounded text-sm">
          Ask
        </button>
      </div>
      <div id="query-answer" class="hidden bg-gray-50 p-4 rounded text-sm text-gray-800 whitespace-pre-wrap"></div>
      <div id="query-sources" class="mt-2 text-xs text-gray-400"></div>
    </div>
  </main>

  <script>
    async function doSearch() {
      const q = document.getElementById('search-input').value.trim();
      const mode = document.getElementById('search-mode').value;
      if (!q) return;
      const results = await api(`/search?q=${encodeURIComponent(q)}&mode=${mode}`);
      const el = document.getElementById('search-results');
      if (!results.length) {
        el.innerHTML = '<p class="text-gray-400 text-sm py-3">No results found.</p>';
        return;
      }
      el.innerHTML = results.map(r => `
        <div class="py-3">
          <div class="font-medium text-sm text-indigo-700">${formatPath(r.file_path)}</div>
          <div class="text-xs text-gray-500 mt-1">${r.snippet || r.chunk_text || ''}</div>
        </div>`).join('');
    }

    async function doQuery() {
      const question = document.getElementById('query-input').value.trim();
      if (!question) return;
      const answerEl = document.getElementById('query-answer');
      answerEl.textContent = 'Thinking...';
      answerEl.classList.remove('hidden');
      const resp = await api('/query', {
        method: 'POST',
        body: JSON.stringify({ question, top_k: 5 })
      });
      answerEl.textContent = resp.answer;
      document.getElementById('query-sources').innerHTML =
        'Sources: ' + resp.sources.map(s => formatPath(s.file_path)).join(', ');
    }
  </script>
</body>
</html>
```

**Step 6: Manual verification**

Start the server:
```
cd E:\DocVault && venv\Scripts\python -m uvicorn api.main:app --reload --port 8000
```

Open browser: http://localhost:8000

Check:
- Dashboard loads with stats cards
- Catalog page shows file list
- Search page has working search bar and RAG query box

**Step 7: Commit**

```
git add frontend/
git commit -m "feat: web frontend — dashboard, catalog, search/RAG"
```

---

## Task 16: Entry Point (`run.py`)

**Files:**
- Create: `E:\DocVault\run.py`

**Step 1: Create `E:\DocVault\run.py`**

```python
"""
DocVault entry point.
Starts the FastAPI web server and spawns background workers in threads.
"""
import threading
import uvicorn
import configparser
import os
from core import manager, ingestor
from workers import extraction_worker, embedding_worker

cfg = configparser.ConfigParser()
cfg.read(os.path.join(os.path.dirname(__file__), 'config.ini'))

DB_PATH = cfg.get('database', 'sqlite_path', fallback='docvault.db')
SCAN_DIR = cfg.get('paths', 'scan_directory', fallback='.')


def start():
    # Init DB
    manager.init_db(DB_PATH)

    # Initial ingest
    print(f"Scanning: {SCAN_DIR}")
    ingestor.ingest(SCAN_DIR, DB_PATH)

    # Start extraction worker in background thread
    t1 = threading.Thread(
        target=extraction_worker.run, args=(DB_PATH,), daemon=True
    )
    t1.start()

    # Start embedding worker in background thread
    t2 = threading.Thread(
        target=embedding_worker.run, args=(DB_PATH,), daemon=True
    )
    t2.start()

    # Start web server (blocking)
    print("Starting DocVault at http://localhost:8000")
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    start()
```

**Step 2: Test end-to-end**

Point `config.ini` at a small test folder, then:

```
cd E:\DocVault && venv\Scripts\python run.py
```

Expected:
- Files ingested, extraction worker picks them up
- Open http://localhost:8000 → dashboard shows files being processed
- Catalog page shows extracted text
- Search page returns results

**Step 3: Commit**

```
git add run.py
git commit -m "feat: run.py entry point — web server + background workers"
```

---

## Dependency Notes

- **Tesseract** must be installed separately (not via pip):
  https://github.com/UB-Mannheim/tesseract/wiki
  Default path: `C:\Program Files\Tesseract-OCR\tesseract.exe`

- **Ollama** must be running locally with `nomic-embed-text` pulled:
  ```
  ollama pull nomic-embed-text
  ollama pull llama3
  ```

- **PyTorch CUDA** — if pip installs CPU build, replace with:
  ```
  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
  ```

- **Qdrant** must be running on NAS at 192.168.1.11:6333 for semantic search to work.
  FTS search works without Qdrant.
