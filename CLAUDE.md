# DocVault — Claude Instructions

## On Every Session Start

Read the following files in full before doing anything else:

1. `docs/internals/status/2026-03-15-project-status.md` — authoritative current state of the project
2. `docs/internals/plans/2026-03-02-future-architecture.md` — planned future architecture (vaults, logs.db, resource governor)
3. `docs/internals/plans/2026-02-26-docvault-design.md` — original design document
4. `docs/internals/plans/2026-02-26-docvault-implementation.md` — original implementation plan

When a new status document appears (e.g. `docs/internals/status/2026-03-06-project-status.md`), read it instead of the previous one — it supersedes all earlier status docs.

Also read all memory topic files referenced in MEMORY.md (the index is auto-loaded but individual files are not):

- `C:\Users\Albert\.claude\projects\E--DocVault\memory\session-notes.md`
- `C:\Users\Albert\.claude\projects\E--DocVault\memory\common-bugs.md`
- `C:\Users\Albert\.claude\projects\E--DocVault\memory\infrastructure-migration.md`
- `C:\Users\Albert\.claude\projects\E--DocVault\memory\project_span_grounding_wip.md`
- `C:\Users\Albert\.claude\projects\E--DocVault\memory\feedback_ui_text_size.md`
- `C:\Users\Albert\.claude\projects\E--DocVault\memory\feedback_visual_companion_windows.md`

If a file listed above does not exist, skip it silently.

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
- qdrant-client v1.17+: use `query_points()` not `search()`
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
- `qdrant_storage/` — vector data, regenerable via Rebuild Index
- `venv/` — Python environment
- `.claude/temp/` — ephemeral Gemini analysis buffers, do not modify
