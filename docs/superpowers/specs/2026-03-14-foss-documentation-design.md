# DocVault FOSS Documentation & Packaging — Design Spec
**Date:** 2026-03-14
**Status:** Approved

---

## Goal

Prepare DocVault for public open-source release on GitHub. The output is a complete
documentation layer and repository hygiene pass — not a code change. The system must
be welcoming to two distinct audiences simultaneously.

---

## Audiences

### Audience A — End users (archivists, researchers, power users)
Technically literate but not developers. They have large document or media collections
and want a self-hosted intelligence layer over them. They care about what DocVault can
*do*, not how the code is structured. The README and setup guide speak to them.

### Audience B — Developers and contributors
Comfortable with Python, Docker, and a terminal. They want to extend DocVault by
writing new extractors, contributing fixes, or understanding the architecture. The
`docs/extractors/`, `docs/architecture.md`, and `CONTRIBUTING.md` speak to them.

**Approach:** Layered. README leads with capability and quickstart. Deep technical docs
live in `docs/` and are linked from the README. Neither audience is forced through the
other's content.

---

## License

**Apache 2.0.** Chosen over MIT because:
- Explicit patent grant protects contributors and users in the AI/ML space
- Permissive enough for broad adoption (no copyleft restrictions)
- Industry standard for AI/ML open source (FastAPI, TensorFlow, Kubernetes)

---

## Hardware Framing

Tiered requirements table with honest expectations. Two tiers:

**Minimum** — any machine with 8 GB RAM, CPU-only Ollama, local Qdrant via Docker.
Text extraction, FTS, basic RAG, and plaintext embeddings work well. Vision AI (image
description, face detection) will be very slow on CPU — measured in minutes per image,
not seconds. Audio transcription (Whisper) is similarly slow without a GPU.

**Recommended** — NVIDIA GPU (8 GB+ VRAM), 32 GB+ RAM, dedicated Qdrant instance.
All features work at full speed. The current reference configuration is a
Ryzen 9 7950X3D, 128 GB RAM, RTX 4090 — benchmarks in the docs reflect this hardware.

Honest framing: DocVault was built and tuned for the recommended tier. The minimum tier
is supported and useful, but vision and audio features are best experienced with a GPU.

---

## Screenshots

Two placeholders in the README:
1. **Hero** — search results page showing hybrid FTS + semantic results with file
   previews. Placeholder text: `[Screenshot: Search results — coming soon]`
2. **Extractor Lab** — the dark terminal-themed lab UI showing a certified kernel in
   the sidebar and a live test run. Placeholder text: `[Screenshot: Extractor Lab — coming soon]`

Screenshots to be captured once the vault has processed a representative document set.

---

## Document Structure

### Root files (new)
| File | Purpose |
|------|---------|
| `README.md` | Hero, features, hardware tiers, quickstart (3 steps), screenshot placeholders, links to docs |
| `LICENSE` | Apache 2.0 full text |
| `CONTRIBUTING.md` | How to write a kernel, PR process, running tests, code conventions |

### docs/ (user-facing, new)
| File | Audience | Purpose |
|------|----------|---------|
| `docs/architecture.md` | B | System overview, data pipeline diagram, component map, three-database layout, worker model |
| `docs/setup.md` | A | Full first-time setup: prerequisites, `setup.ps1`, config questions, Ollama model pulls, verification |
| `docs/configuration.md` | A+B | Every setting key, group, default, and description. config.ini reference. settings.db overrides. |
| `docs/troubleshooting.md` | A | Port conflicts, DB corruption, Qdrant down, WMI access denied, Ollama no slots, charmap errors |
| `docs/extractors/contract.md` | B | The kernel contract: MANIFEST dict, required signatures, BaseExtractor/ExtractorContext/IngestResult, certification lifecycle |
| `docs/extractors/writing-an-extractor.md` | B | Step-by-step guide with a fully worked example kernel from scratch |
| `docs/extractors/catalogue.md` | A+B | Every built-in extractor: what it does, what it extracts, dependencies, file types handled |
| `docs/tools/test-kernel.md` | B | Expand existing docs/test-kernel-cli.md into full tool reference |
| `docs/tools/build-art-index.md` | B | How to seed and extend the art identification index |

### docs/internals/ (moved, not public-facing)
Existing planning and session docs move here. Not linked from README — they are
development history, not user documentation.

| Source | Destination |
|--------|-------------|
| `docs/plans/` | `docs/internals/plans/` |
| `docs/status/` | `docs/internals/status/` |
| `docs/SideQuests.md` | `docs/internals/SideQuests.md` |
| `docs/RAGUpdate.md` | `docs/internals/RAGUpdate.md` |
| `docs/vision.md` | `docs/internals/vision.md` |
| `docs/test-kernel-cli.md` | expanded into `docs/tools/test-kernel.md`, original removed |

---

## README Structure

1. **Badge row** — license, Python version
2. **One-line description** — what DocVault is
3. **Hero screenshot placeholder**
4. **Feature highlights** — bulleted, capability-first, grouped by category
5. **Hardware requirements** — two-tier table with honest expectations
6. **Quickstart** — three steps: clone, `.\setup.ps1`, `.\start.ps1`
7. **What gets extracted** — table of file types and what DocVault pulls from each
8. **Architecture overview** — two-paragraph summary, link to `docs/architecture.md`
9. **Writing extractors** — two sentences + link to `docs/extractors/contract.md`
10. **Management scripts** — start / setup / reset explained in one line each
11. **Contributing** — link to `CONTRIBUTING.md`
12. **License** — one line

---

## CONTRIBUTING.md Structure

1. Welcome + philosophy (maximum extraction, local-first)
2. Development setup (venv, running tests)
3. Writing a new extractor — link to full guide, key rules summarised
4. PR conventions (commit message style, test requirements)
5. Code conventions (settings at call time, route ordering, Pydantic v2 patterns)
6. Known limitations / side quests (link to internals/SideQuests.md)

---

## Constraints

- No code changes in this work. Documentation only.
- Internal docs (plans, status) move to `docs/internals/` but are not deleted — they
  are development history and remain in git.
- The `docs/superpowers/` directory (this spec and future specs) stays as-is.
- All new docs are written in GitHub-flavoured Markdown, rendered correctly in the
  GitHub UI without any external tooling.
- Windows-first setup instructions (the reference platform), with notes where
  Linux/macOS paths differ.
