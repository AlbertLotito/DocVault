# DocVault — Claude Instructions

## On Every Session Start

Read the following in full before doing anything else:

### 1. Current Status (authoritative project state)

The status doc is the most recently dated file in `docs/internals/status/`. Always read the newest one — it supersedes all earlier status docs. As of the last update the file is:

`docs/internals/status/2026-05-31-project-status.md`

If a newer file exists in that directory, read that instead.

### 2. Memory Files (the index MEMORY.md is auto-loaded; read the individual files below)

- `C:\Users\Albert\.claude\projects\E--DocVault\memory\session-notes.md`
- `C:\Users\Albert\.claude\projects\E--DocVault\memory\common-bugs.md`
- `C:\Users\Albert\.claude\projects\E--DocVault\memory\infrastructure-migration.md`
- `C:\Users\Albert\.claude\projects\E--DocVault\memory\project_span_grounding_wip.md`
- `C:\Users\Albert\.claude\projects\E--DocVault\memory\feedback_ui_text_size.md`
- `C:\Users\Albert\.claude\projects\E--DocVault\memory\feedback_visual_companion_windows.md`

Also check `C:\Users\Albert\.claude\projects\E--DocVault\memory\MEMORY.md` for any additional topic files added since this list was last updated and read those too.

If any file listed above does not exist, skip it silently.

## Project Location

`E:\DocVault`

---

## Agent Roles

- **Claude Code** — Primary agent. Handles implementation, editing, and planning.
- **Gemini CLI** — Secondary agent. Handles deep codebase scans that exceed practical context limits.

## Delegation Rules for Claude

- Delegate to the `gemini-analyzer` subagent when a task requires reading more than 30 files or analyzing files larger than 100KB.
- Always pipe Gemini output to `.claude/temp/analysis.md` — never stream large output into the main session context.
- Read only the summary from Gemini's output. Reference `.claude/temp/analysis.md` for full details.

## Security Rules

- Never enable YOLO mode persistently in any settings file.
- Use `--yolo` on the CLI only, only for read-only scans, and only when running interactively.

---

## Key Conventions

- All configurable values must be read from `settings.get()` at call time, never at import time
- Settings resolution chain: vault_settings → settings.db → config.ini → schema default
- `file_hash` (SHA-256) is the primary key for all documents
- Route ordering: specific routes (e.g. `/catalog/inspect`) must be declared BEFORE parameterised routes (`/catalog/{hash}`)
- Worker threads are daemon threads — no graceful drain on exit
- Vector storage is LanceDB only (no external service) — `merge_insert()` for upsert, `list_tables().tables` for membership checks; see `embeddings/vector_store.py` (main store) and `embeddings/art_vector_store.py` (art_index)
- Pydantic v2: optional fields need `str | None = None` not `str = None`
- New API endpoints require a server restart before they are available

## Database Files

| File | Purpose |
|---|---|
| `docvault.db` | Operational data — tasks, FTS5, extracted images |
| `settings.db` | User configuration — survives data resets |
| `logs.db` | Observability — timings, errors, stats (planned) |

## Do Not Touch

- `credentials/` — OAuth secrets, gitignored
- `.cache/` — extracted artefacts, regenerable
- `lancedb_storage/` — vector data (main store + art_index), regenerable via Rebuild Index
- `qdrant_storage.migrated-backup/` — decommissioned Qdrant data, kept as a backup pending cleanup (see 2026-07 status doc); not regenerable without re-running `tools/build_art_index.py`
- `venv/` — Python environment
- `.claude/temp/` — ephemeral Gemini analysis buffers, do not modify
