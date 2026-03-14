> **Advanced / Optional** — This guide is only relevant if you want DocVault to identify paintings and artworks by name and artist when it encounters them in photos or scanned catalogues. Building the full index takes several hours and roughly 70 GB of staging storage.

---

# Art Index — Seeding Guide

## 1. What the art index is

The art index is a separate Qdrant collection (`art_index`) containing CLIP visual embeddings of known artworks drawn from two public-domain datasets. When the art enrichment worker processes an image, it compares the image against this index. A match above the similarity threshold enriches the document's Qdrant payload with artwork metadata (title, artist, source URL, dataset origin) — making photos of known artworks searchable by artwork name or artist.

Key points:

- The `art_index` collection is completely separate from the main `docvault` collection.
- `reset.ps1` never touches `art_index` — it survives clean-slate resets.
- The art index is optional — if empty (or absent), the art enrichment worker skips identification silently.
- Embeddings use OpenAI CLIP (ViT-B/32, 512-dim, cosine distance), not Ollama. CLIP runs locally on CPU or GPU.

---

## 2. What `tools/build_art_index.py` does

The tool builds the `art_index` from two large open art datasets:

| Dataset | Source | Items | Staging storage |
|---|---|---|---|
| **WikiArt** | HuggingFace Datasets (`huggan/wikiart`) | ~130 k paintings | ~50 GB (HF cache) |
| **MET Open Access** | Metropolitan Museum of Art | ~115 k public-domain works | ~18 GB (images) |

For each dataset the tool:

1. Downloads the dataset (WikiArt via HuggingFace Datasets; MET via a metadata CSV from GitHub and individual images from the MET CDN).
2. Runs each image through CLIP (ViT-B/32) to produce a normalised 512-dim vector.
3. Upserts the vector and metadata payload (title, artist, source URL, dataset) to the `art_index` Qdrant collection in batches of 64.

**Resumable.** Progress is tracked in a SQLite file (`.index_progress.db`) in the cache directory. Interrupted runs pick up exactly where they left off — already-indexed items are skipped. MET image URLs (which must be resolved via the MET collection API) are also cached so they are not re-fetched on resume.

**Idempotent.** Each point uses a deterministic UUID5 derived from `source:key`, so re-running with unchanged data is safe.

**Permanent artefact.** The Qdrant `art_index` collection (~2 GB) is the only permanent output. Downloaded images and the HuggingFace cache are staging artefacts kept in the DocVault cache directory.

---

## 3. Prerequisites

Install the additional dependencies before running (these are not part of the standard DocVault requirements):

```bash
pip install openai-clip torch torchvision datasets tqdm requests qdrant-client
```

CLIP downloads the ViT-B/32 model weights (~600 MB) on first run. If a CUDA-capable GPU is available, CLIP uses it automatically; otherwise it runs on CPU (slower but functional).

Qdrant must be running before you start:

```bash
# Verify Qdrant is up
curl http://localhost:6333/collections
```

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

# Override Qdrant connection (defaults come from DocVault settings)
python tools/build_art_index.py --qdrant-host localhost --qdrant-port 6333

# Provide a HuggingFace access token (not required for huggan/wikiart today,
# but prevents auth prompts if the dataset becomes gated in future)
python tools/build_art_index.py --hf-token hf_xxxxxxxxxxxx

# Reduce CLIP batch size if you run out of GPU/CPU memory (default: 64)
python tools/build_art_index.py --batch 32

# Use a non-default Qdrant collection name
python tools/build_art_index.py --collection art_index_v2
```

### Flag reference

| Flag | Default | Description |
|---|---|---|
| `--source` | `all` | `wikiart`, `met`, or `all` |
| `--cache-dir` | From `paths:cache_directory` setting | Where to store downloaded datasets |
| `--collection` | `art_index` | Qdrant collection name |
| `--batch` | `64` | CLIP embedding batch size; reduce to `32` if OOM |
| `--limit` | _(none)_ | Cap items per source — useful for smoke-testing |
| `--qdrant-host` | From DocVault settings | Qdrant hostname |
| `--qdrant-port` | From DocVault settings | Qdrant port |
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

```bash
# How many artworks are indexed?
curl http://localhost:6333/collections/art_index
```

```python
# Or from Python
from qdrant_client import QdrantClient
c = QdrantClient('localhost', port=6333)
print(c.get_collection('art_index'))
```

Use `--verify` to do a quick quality check against randomly sampled MET images:

```bash
python tools/build_art_index.py --verify
```

This loads 5 random images from the MET staging cache, embeds them with CLIP, queries `art_index`, and prints the top match score and metadata for each — letting you eyeball retrieval quality before putting the index into production.

---

## 7. Extending the index

Re-run `build_art_index.py` at any time with the same or additional flags. Already-indexed points are skipped automatically via the progress DB. New items are upserted incrementally.

If you add a second custom collection (e.g. `--collection art_index_v2`), the enrichment worker will need to be pointed at it via configuration — the default target is always `art_index`.

---

## 8. Similarity threshold

Art identification uses the `embeddings:score_threshold` setting (the same threshold as general semantic search). The default (`0.65`) works well for clear photographs of artworks.

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
| Qdrant `art_index` | ~2 GB | `qdrant_storage/` | **Yes — the permanent output** |

Once indexing is complete and you have verified quality with `--verify`, the staging images and HuggingFace cache can be deleted to reclaim disk space. The Qdrant collection is self-contained.
