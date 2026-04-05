# Priority-Aware Embedding + Tunable Aging Design

## Goal

Make the embedding worker respect vault priority (so high-priority vaults embed sooner), and make the aging weight tunable for both extraction and embedding workers via settings.

## Background

The extraction worker (`claim_pending_task`) already uses a composite priority score:

```
effective_priority = (10 - vault_priority) * extractor_priority + age_bonus
where age_bonus = seconds_since_last_update / 3600
```

The embedding worker (`claim_extracted_task`, `claim_extracted_tasks`) uses plain FIFO (`ORDER BY last_update`) — vault priority has no effect on embedding order. The `3600` divisor in the extraction formula is hardcoded with no way to tune it.

## Architecture

Two new settings control aging speed for each worker. Both claim functions in `core/manager.py` are updated: extraction to use the tunable divisor, embedding to add a `LEFT JOIN vaults` and the same priority + aging formula. Settings are read at call time (the project's established pattern), so changes take effect on the next claim cycle without a restart.

## Settings

Two new entries added to the `workers` group in `core/settings.py`:

```python
'workers:extract_age_weight': {
    'type': 'int', 'default': 3600,
    'label': 'Extraction age weight (s per priority point)', 'group': 'embeddings',
    'description': 'Seconds a PENDING task must wait to gain 1 priority point. Lower = ages faster.'
},
'workers:embed_age_weight': {
    'type': 'int', 'default': 900,
    'label': 'Embedding age weight (s per priority point)', 'group': 'embeddings',
    'description': 'Seconds an EXTRACTED task must wait to gain 1 priority point. Lower = ages faster.'
},
```

`group: 'embeddings'` matches the existing convention for `workers:embed_batch_size` and `workers:embed_concurrency` — all worker throughput settings appear under the Embeddings section of the Settings UI.

Both appear in the Settings UI automatically via the schema — no UI changes required.

## Schema Changes

None. No new columns or tables needed.

## Components

### `core/manager.py` — `claim_pending_task`

The hardcoded `/ 3600.0` in the ORDER BY clause is replaced with a bound parameter. The setting is read at call time:

```python
def claim_pending_task(db_path, worker_id):
    age_weight = settings.get('workers:extract_age_weight') or 3600
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT t.file_hash, t.file_path, t.file_type, t.priority, t.vault_id,
                      t.parent_hash, t.metadata_json,
                      COALESCE(v.priority, 5) AS vault_priority
               FROM tasks t
               LEFT JOIN vaults v ON t.vault_id = v.vault_id
               WHERE t.status = 'PENDING'
               ORDER BY
                 ((10 - COALESCE(v.priority, 5)) * COALESCE(t.priority, 10))
                 + (CAST(
                     (julianday('now') - julianday(t.last_update)) * 86400
                    AS REAL) / ?)
                 DESC
               LIMIT 1""",
            (age_weight,)
        ).fetchone()
        ...
```

### `core/manager.py` — `claim_extracted_task` and `claim_extracted_tasks`

Both functions add a `LEFT JOIN vaults` and replace `ORDER BY last_update` with the vault priority + aging formula. There is no `* extractor_priority` term — all EXTRACTED tasks are equivalent in extractor priority; only vault priority and age matter.

```python
def claim_extracted_tasks(db_path, worker_id, limit=8):
    age_weight = settings.get('workers:embed_age_weight') or 900
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """SELECT t.file_hash, t.file_path, t.file_type, t.extracted_text
               FROM tasks t
               LEFT JOIN vaults v ON t.vault_id = v.vault_id
               WHERE t.status = 'EXTRACTED'
               ORDER BY
                 (10 - COALESCE(v.priority, 5))
                 + (CAST(
                     (julianday('now') - julianday(t.last_update)) * 86400
                    AS REAL) / ?)
                 DESC
               LIMIT ?""",
            (age_weight, limit)
        ).fetchall()
        ...
```

`claim_extracted_task` (single-task variant) gets identical treatment — same JOIN, same ORDER BY, `LIMIT 1`, single bound parameter `(age_weight,)`:

```python
def claim_extracted_task(db_path, worker_id):
    age_weight = settings.get('workers:embed_age_weight') or 900
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT t.file_hash, t.file_path, t.file_type, t.extracted_text
               FROM tasks t
               LEFT JOIN vaults v ON t.vault_id = v.vault_id
               WHERE t.status = 'EXTRACTED'
               ORDER BY
                 (10 - COALESCE(v.priority, 5))
                 + (CAST(
                     (julianday('now') - julianday(t.last_update)) * 86400
                    AS REAL) / ?)
                 DESC
               LIMIT 1""",
            (age_weight,)
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
```

### Priority Formula — Embedding

```
effective_priority = (10 - vault_priority) + age_bonus
where age_bonus = seconds_since_last_update / embed_age_weight
```

- `vault_priority` ranges 1–9 (default 5). Priority 1 vault: score starts at 9. Priority 9 vault: score starts at 1.
- With `embed_age_weight = 900` (15 min per point), a priority-9 vault's tasks need 8 × 900 = 7200 seconds (2 hours) to match a fresh priority-1 task.
- `COALESCE(v.priority, 5)` gives orphaned tasks (no vault row) a neutral score of 5.

## Interaction with Global Pause

Unaffected. The `should_pause_or_throttle()` check in the embedding worker runs before any claim call — pausing still halts all embedding regardless of priority.

## Error Handling

- `COALESCE(v.priority, 5)` — safe default for tasks with no matching vault row.
- Settings read at call time — if `workers:embed_age_weight` is missing or returns `None`, this would cause a division error. Guard: `age_weight = settings.get('workers:embed_age_weight') or 900`. The `or` short-circuit also catches `0`, which would cause a SQLite division-by-zero error and is an invalid value regardless. Both `None` and `0` correctly fall back to the default. Apply the same `or 3600` guard to `extract_age_weight` in `claim_pending_task`. Note: `from core.settings import settings` is already present at module level in `core/manager.py` — no new import needed.
- No migration needed — no schema changes.

## Testing

`tests/test_embed_priority.py` — 4 tests:

1. `test_embed_priority_high_vault_first` — two EXTRACTED tasks in different vaults; task from higher-priority vault (lower number) is claimed first
2. `test_embed_priority_aging_overtakes` — lower-priority vault task with old `last_update` overtakes a fresh higher-priority task (simulate with backdated timestamp and small age_weight)
3. `test_embed_priority_orphaned_task` — task with no matching vault row is claimed with neutral priority (does not error, does not block queue)
4. `test_extract_age_weight_respected` — `claim_pending_task` respects `workers:extract_age_weight`: with a very small age_weight, an old low-priority task overtakes a fresh high-priority task
