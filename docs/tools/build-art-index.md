> **Advanced / Optional** — This guide is only relevant if you want DocVault to identify paintings and artworks by name and artist when it encounters them in photos or scanned catalogues. Building the full index takes several hours and roughly 70 GB of staging storage.

---

# Art Index — Seeding Guide

## 1. What the art index is

The art index is a separate LanceDB table (`art_index`, in `lancedb_storage/` alongside the main `docvault` table) containing CLIP visual embeddings of known artworks drawn from two public-domain datasets. When the art enrichment worker processes an image, it compares the image against this table. A match above the similarity threshold enriches the image's `.nfo` sidecar with artwork metadata (title, artist, source URL, dataset origin) — making photos of known artworks searchable by artwork name or artist.

Key points:

- The `art_index` table is completely separate from the main `docvault` table — different vector dimension (512 CLIP vs 768 nomic-embed-text) and different payload shape.
- `reset.ps1` never touches `lancedb_storage/` at all, so `art_index` survives clean-slate resets along with everything else there.
- The art index is optional — if the table is empty (or absent), the art enrichment worker skips identification silently.
- Embeddings use OpenAI CLIP (ViT-B/32, 512-dim, cosine distance), not Ollama. CLIP runs locally on CPU or GPU. No external database service is required — LanceDB is embedded, same as the main document vector store.

---

## 2. What `tools/build_art_index.py` does

The tool builds the `art_index` table from two large open art datasets:

| Dataset | Source | Items | Staging storage |
|---|---|---|---|
| **WikiArt** | HuggingFace Datasets (`huggan/wikiart`) | ~130 k paintings | ~50 GB (HF cache) |
| **MET Open Access** | Metropolitan Museum of Art | ~115 k public-domain works | ~18 GB (images) |

For each dataset the tool:

1. Downloads the dataset (WikiArt via HuggingFace Datasets; MET via a metadata CSV from GitHub and individual images from the MET CDN).
2. Runs each image through CLIP (ViT-B/32) to produce a normalised 512-dim vector.
3. Upserts the vector and metadata payload (title, artist, source URL, dataset) to the `art_index` LanceDB table in batches of 64 (`merge_insert` — safe to re-run).

**Resumable.** Progress is tracked in a SQLite file (`.index_progress.db`) in the cache directory. Interrupted runs pick up exactly where they left off — already-indexed items are skipped. MET image URLs (which must be resolved via the MET collection API) are also cached so they are not re-fetched on resume.

**Idempotent.** Each row uses a deterministic UUID5 derived from `source:key`, so re-running with unchanged data is safe.

**Permanent artefact.** The LanceDB `art_index` table (~2 GB, inside `lancedb_storage/`) is the only permanent output. Downloaded images and the HuggingFace cache are staging artefacts kept in the DocVault cache directory.

---

## 3. Prerequisites

Install the additional dependencies before running (these are not part of the standard DocVault requirements):

```bash
pip install openai-clip torch torchvision datasets tqdm requests
```

CLIP downloads the ViT-B/32 model weights (~600 MB) on first run. If a CUDA-capable GPU is available, CLIP uses it automatically; otherwise it runs on CPU (slower but functional).

No external service needs to be running — `lancedb_storage/` is created automatically on first write.

---

## 4. Usage

```bash
# Full index — both WikiArt and MET (recommended; takes several hours)
python tools/build_art_index.py

# Single source
python tools/build_art_index.py --source wikiart
python tools/build_art_index.py --source met

# Smoke-test — first 500 items per source (fast, useful for verifying setup)
python tools/build_art_index.py --limit 500

# Resume an interrupted run (no extra flags needed — progress is automatic)
python tools/build_art_index.py

# Spot-check the index quality with 5 random MET images
python tools/build_art_index.py --verify

# Override the staging cache directory
python tools/build_art_index.py --cache-dir D:\art_staging

# Provide a HuggingFace access token (not required for huggan/wikiart today,
# but prevents auth prompts if the dataset becomes gated in future)
python tools/build_art_index.py --hf-token hf_xxxxxxxxxxxx

# Reduce CLIP batch size if you run out of GPU/CPU memory (default: 64)
python tools/build_art_index.py --batch 32

# Use a non-default LanceDB table name
python tools/build_art_index.py --table art_index_v2
```

### Flag reference

| Flag | Default | Description |
|---|---|---|
| `--source` | `all` | `wikiart`, `met`, or `all` |
| `--cache-dir` | From `paths:cache_directory` setting | Where to store downloaded datasets |
| `--table` | `art_index` | LanceDB table name |
| `--batch` | `64` | CLIP embedding batch size; reduce to `32` if OOM |
| `--limit` | _(none)_ | Cap items per source — useful for smoke-testing |
| `--hf-token` | From `huggingface:access_token` setting or `HF_TOKEN` env var | HuggingFace access token |
| `--verify` | _(flag)_ | Spot-check index with random images instead of building |

---

## 5. HuggingFace token

The WikiArt dataset (`huggan/wikiart`) is public and does not require a token today. You can supply one anyway to avoid auth prompts if the dataset ever becomes gated:

- Pass `--hf-token hf_xxxxxxxxxxxx` on the command line, or
- Set the `HF_TOKEN` environment variable, or
- Add `huggingface:access_token` to DocVault settings (Settings UI → Advanced).

---

## 6. Checking the index

```python
from embeddings.art_vector_store import ArtVectorStore
store = ArtVectorStore()
print(store.count(), "artworks indexed")
```

Use `--verify` to do a quick quality check against randomly sampled MET images:

```bash
python tools/build_art_index.py --verify
```

This loads 5 random images from the MET staging cache, embeds them with CLIP, queries `art_index`, and prints the top match score and metadata for each — letting you eyeball retrieval quality before putting the index into production.

---

## 7. Extending the index

Re-run `build_art_index.py` at any time with the same or additional flags. Already-indexed rows are skipped automatically via the progress DB. New items are upserted incrementally.

If you add a second custom table (e.g. `--table art_index_v2`), the enrichment worker will need to be pointed at it via the `art:clip_table` setting — the default target is always `art_index`.

---

## 8. Similarity threshold

Art identification uses `art:clip_accept_threshold` (default `0.80`) and `art:clip_fallback_threshold` (default `0.70`) — see [docs/internals/vision.md](../internals/vision.md) for the full acceptance logic.

Adjust in the Settings UI:

- **Lower threshold** (e.g. `0.50`) → more matches, more false positives. Useful for scanned catalogues or low-quality reproductions.
- **Higher threshold** (e.g. `0.80`) → fewer matches, more precision. Suitable for high-quality reference photographs.

---

## 9. Storage summary

| Artefact | Size | Location | Permanent? |
|---|---|---|---|
| WikiArt HuggingFace cache | ~50 GB | `<cache_dir>/art_datasets/wikiart/` | No — can be deleted after indexing |
| MET images | ~18 GB | `<cache_dir>/art_datasets/met/images/` | No — can be deleted after indexing |
| Progress DB | Small | `<cache_dir>/art_datasets/.index_progress.db` | Keep if you may resume |
| CLIP model weights | ~600 MB | Torch cache (`~/.cache/torch/`) | Keep — re-downloaded if deleted |
| LanceDB `art_index` table | ~2 GB | `lancedb_storage/` | **Yes — the permanent output** |

Once indexing is complete and you have verified quality with `--verify`, the staging images and HuggingFace cache can be deleted to reclaim disk space. The LanceDB table is self-contained.
