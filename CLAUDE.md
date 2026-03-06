# DocVault — Claude Instructions

## On Every Session Start

Read the following files in full before doing anything else:

1. `docs/status/2026-03-05-project-status.md` — authoritative current state of the project
2. `docs/plans/2026-03-02-future-architecture.md` — planned future architecture (vaults, logs.db, resource governor)
3. `docs/plans/2026-02-26-docvault-design.md` — original design document
4. `docs/plans/2026-02-26-docvault-implementation.md` — original implementation plan

When a new status document appears (e.g. `docs/status/2026-03-03-project-status.md`), read it instead of the previous one — it supersedes all earlier status docs.

## Project Location

`E:\DocVault`

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
