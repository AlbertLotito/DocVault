# Project Status — 2026-08-01

Supersedes all earlier status documents.

---

## 1–34. Previous Work — COMPLETE

See `docs/internals/status/2026-07-19-project-status.md` for full details on all prior work
(Span Grounding, Qdrant Removal, Art Enrichment Reliability, Windows Ollama Port-Exclusion
Auto-Recovery, Claude API Key & Model as Settings, Deleted-File Detection, and everything
referenced from earlier status docs).

---

## 35. Drive Migration E: → D: + docvault.db Corruption Recovery — COMPLETE (2026-07-26–08-01)

### Why

The project lived on `E:\DocVault`, which turned out to be a physical HDD in a **Sabrent Dual
SATA Bridge (external USB dock)**, not an internal drive. Given DocVault's workload — SQLite
under WAL with frequent small transactions, LanceDB touching many small fragment files, and an
extraction pipeline writing thousands of small files into `.cache` — this is close to a
worst-case storage profile (high random-IOPS, low-latency-sensitive access on a device that's
weak at exactly that). The project was moved to `D:\DocVault`, an internal NVMe SSD.

### What Happened

| Step | Outcome |
|---|---|
| Hardcoded-path audit | `config.ini`, `start.ps1`, and all `.py` app code already used relative paths / `settings.get()`. Only `setup.ps1` (interactive-setup defaults) and `reset.ps1` (one literal cache path) hardcoded `E:\DocVault\...` — both fixed to relative/`$scriptDir`-derived paths. `tests/rigs/check_db_migration.py`'s docstring example updated too (cosmetic). |
| First copy attempts (robocopy) | Failed twice — not a syntax issue. Windows Event Log (System, ID 153, "IO operation...was retried") showed **Disk 2 (the Sabrent dock, backing E:)** throwing sustained read-retry storms under `/MT:16` load, escalating from failing 96GB deep on attempt 1 to failing instantly on the root directory on attempt 2. Root cause: a faulty USB cable — fine for light access, dropped out under sustained heavy multi-threaded reads. Fixed by replacing the cable. |
| Bulk copy | Completed via TeraCopy after the cable fix. |
| **TeraCopy gotcha** | TeraCopy silently skipped the entire `lancedb_storage` folder's file contents (~97,603 files / 75.7GB) while still creating the full nested directory skeleton — no files, but no visible top-level failure either. Not a hidden-file or path-length issue (verified with `-Force`, and max path length was only 104 chars). Re-copied via a targeted `robocopy /MIR` pass on just that folder once the cable was fixed — completed cleanly, 97,603/97,603, 0 failed. **Lesson: always verify byte-for-byte / count-for-count after a TeraCopy job on critical data — "job finished" alone isn't sufficient proof of completeness.** |
| Full verification | Every top-level folder matched exactly (file count + byte size) between E: and D:, including `lancedb_storage`, `.cache` (241,684 files), `qdrant_storage.migrated-backup`, `DataBackup`. |
| **`docvault.db` corruption discovered** | `PRAGMA integrity_check` failed identically on **both** E: and D: copies — proving the corruption pre-dated the migration entirely (most likely the same flaky USB connection corrupting the live DB during ordinary use, before this migration effort began). Not caused by the copy process. |
| Corruption scope | Table-by-table diagnosis showed corruption was confined to the FTS5 virtual table's shadow tables (`fts_index_data`, `fts_index_content`) plus one row in `extracted_texts`. All core tables — `tasks` (188,462 rows), `extracted_texts` (101,591 rows), `extracted_images`, `vaults`, `file_vault`, `art_enrichment_issues` — were intact. **Gotcha: `SELECT COUNT(*)` succeeded on `extracted_texts` even though a full `SELECT *` later failed on one row** — COUNT can use an index/rowid scan that never touches a large TEXT column's overflow pages, so it's not a reliable corruption signal on its own. |
| Repair strategy | Built a fresh `docvault_recovered.db`: replayed the exact `CREATE TABLE`/`CREATE INDEX` statements for every non-FTS table from the corrupted DB's own `sqlite_master`, bulk-copied each table (fast path), and fell back to row-by-row recovery (enumerate primary keys, fetch+insert one row at a time) for any table where the bulk read raised `database disk image is malformed`. Only `extracted_texts` needed the fallback; every other table bulk-copied cleanly. **Gotcha: `sqlite_sequence` cannot be explicitly `CREATE`d (reserved name) — exclude it from schema-replay scripts. SQLite auto-derives the correct autoincrement counter from `MAX(rowid)` in the actual data regardless, so nothing needs to be restored for it.** FTS index was then rebuilt from scratch by iterating every recovered `extracted_texts` row through `manager.update_fts()` — the app's own single-file FTS-sync function — rather than attempting to repair the FTS5 shadow tables directly. |
| Repair result | **101,589 of 101,591 `extracted_texts` rows recovered (99.998%)**. The 2 unrecoverable rows were low-stakes: a `skimage/draw/draw.py` library file incidentally scanned from a nested `stable-diffusion-webui` venv, and one regenerable `.cache/extracted_images/...` derived image. Both reset to `tasks.status = 'PENDING'` for automatic reprocessing — confirmed picked up correctly on the next server start (`Starting extraction: draw.py`, `Biometric Analysis: page_019_img_001.png`). Final `PRAGMA integrity_check` on the repaired DB returns `ok`; a live `MATCH` query against the rebuilt FTS index was confirmed working. Old corrupted file kept as `docvault.db.corrupted-20260728` (untracked, not committed) rather than deleted. |
| Windows Defender exclusion | Moved from `E:\DocVault` to `D:\DocVault` (`Remove-MpPreference`/`Add-MpPreference -ExclusionPath`) — this was the original fix for a startup-hang bug (Defender scanning `docvault.db` on first open); needed re-pointing at the new location. |
| Verified startup | Full `start.ps1` run from `D:\DocVault` confirmed: `Embedding worker: vector store ready.` (LanceDB fix holds), no malformed-DB errors, extraction/embedding both processing normally. Stall-sensor `ERROR` messages on that run (~5371 min since last activity) were expected — an artifact of the server having been stopped for ~3.7 days during migration/repair, not a new problem. |

### Recurring False Alarm — Worth Remembering

During this migration, `Moved: <old path> → <garbled path>` log lines from `core/ingestor.py`
triggered alarm twice (this doc's author included) before being correctly diagnosed as harmless.
**This is a cosmetic false-positive, not data loss**: many old Office-embedded boilerplate parts
(`clip_colorschememapping.xml`, `clip_preview.wmf`, etc.) are byte-identical across thousands of
old `.doc`/`.docx` files. Since DocVault tracks content by hash, when the same boilerplate exists
at many real paths, the ingestor's move-detection just flips which path it considers canonical
between scans and logs it as "moved." Confirmed via code (`core/ingestor.py:238-246`): this path
only calls `manager.update_task_path()` / `_update_vector_path()` — it **never** calls
`os.rename()` or touches the filesystem. Confirmed on disk too: both the "old" and "new" paths
coexist, and the "new" path's file predates the scan by years.

### Remaining Step

- Old `E:\DocVault` still needs to be renamed to `E:\DocVault.bak` once the D: copy has run
  stably for a bit longer — not yet done as of this doc.

---

## 36. Known Issues (supersedes §34 of the 2026-07-19 doc)

- **WMI CPU temp sensor** — still fails on some machines with COM error 0x80041003 (unrelated to the drive migration). Falls back to dummy (0°C). Non-critical.
- **SQLite lock contention** — full fix (write serialisation) still deferred; should be materially less likely to recur now that the DB lives on internal NVMe instead of an external USB HDD.
- **`Moved:` log noise** — cosmetic false-positive for hash-duplicate boilerplate content, see above. Not yet fixed at the source (still logs "moved" when it's actually "same hash, different pre-existing duplicate") — low priority, cosmetic only.
