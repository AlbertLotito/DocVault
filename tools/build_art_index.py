#!/usr/bin/env python3
"""
Build the local CLIP art identification index in LanceDB (`art_index` table).

Downloads and indexes two open art datasets:

  WikiArt  -- ~130 k paintings via HuggingFace Datasets (huggan/wikiart)
  MET      -- ~115 k public-domain works from the Metropolitan Museum of Art
               (metadata CSV from GitHub + images from the MET CDN)

Downloaded files are kept in the DocVault cache directory for your review.
The LanceDB `art_index` table (~2 GB, in lancedb_storage/) is the only
permanent artefact. No external service required — LanceDB is embedded,
same as the main document vector store.

Usage
-----
  # Full index (both sources — recommended)
  python tools/build_art_index.py

  # Single source
  python tools/build_art_index.py --source wikiart
  python tools/build_art_index.py --source met

  # Smoke-test (first N items per source)
  python tools/build_art_index.py --limit 500

  # Resume an interrupted run
  python tools/build_art_index.py          # progress is tracked; already-indexed items are skipped

  # Override cache directory
  python tools/build_art_index.py --cache-dir D:\\art_staging

  # Use a non-default LanceDB table name
  python tools/build_art_index.py --table art_index_v2

Requirements (one-time install)
--------------------------------
  pip install openai-clip torch torchvision datasets tqdm requests

Staging storage needed
----------------------
  WikiArt (HuggingFace cache)   ~50 GB
  MET images                    ~18 GB
  LanceDB art_index (permanent) ~2 GB
"""

import argparse
import csv
import hashlib
import os
import sqlite3
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# ── Resolve DocVault root so we can import settings ──────────────────────────

_HERE     = Path(__file__).resolve().parent
_ROOT     = _HERE.parent
sys.path.insert(0, str(_ROOT))

# ── Constants ─────────────────────────────────────────────────────────────────

TABLE_NAME      = 'art_index'
CLIP_DIM        = 512          # ViT-B/32 output dimension
MET_CSV_URL     = (
    'https://github.com/metmuseum/openaccess/raw/master/MetObjects.csv'
)
MET_WORKERS     = 10           # concurrent image download threads

_ID_NAMESPACE = uuid.UUID('12345678-1234-5678-1234-567812345678')


# ── Progress tracker (SQLite) ─────────────────────────────────────────────────

class ProgressDB:
    """Lightweight SQLite tracker so interrupted runs resume cleanly."""

    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS indexed "
            "(id TEXT PRIMARY KEY, source TEXT, indexed_at TEXT)"
        )
        # Cache MET API responses (object_id → image_url) so we don't
        # re-fetch on resume. The MET CSV no longer includes image URLs
        # (removed ~2023); they must be fetched per-object from the API.
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS met_image_urls "
            "(object_id TEXT PRIMARY KEY, image_url TEXT, fetched_at TEXT)"
        )
        self._conn.commit()

    def get_met_image_url(self, object_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT image_url FROM met_image_urls WHERE object_id = ?",
            (object_id,)
        ).fetchone()
        return row[0] if row else None

    def set_met_image_url(self, object_id: str, image_url: str):
        self._conn.execute(
            "INSERT OR REPLACE INTO met_image_urls (object_id, image_url, fetched_at) "
            "VALUES (?, ?, ?)",
            (object_id, image_url, datetime.utcnow().isoformat())
        )
        self._conn.commit()

    def is_done(self, point_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM indexed WHERE id = ?", (point_id,)
        ).fetchone()
        return row is not None

    def mark_done(self, point_id: str, source: str):
        self._conn.execute(
            "INSERT OR IGNORE INTO indexed (id, source, indexed_at) VALUES (?,?,?)",
            (point_id, source, datetime.utcnow().isoformat())
        )
        self._conn.commit()

    def count(self, source: str | None = None) -> int:
        if source:
            return self._conn.execute(
                "SELECT COUNT(*) FROM indexed WHERE source = ?", (source,)
            ).fetchone()[0]
        return self._conn.execute(
            "SELECT COUNT(*) FROM indexed"
        ).fetchone()[0]

    def close(self):
        self._conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def point_id(source: str, key: str) -> str:
    """Deterministic UUID5 from source + key — safe to re-run."""
    return str(uuid.uuid5(_ID_NAMESPACE, f"{source}:{key}"))


def _read_cache_dir() -> str:
    """Read paths:cache_directory from DocVault settings."""
    try:
        from core.settings import settings
        d = settings.get('paths:cache_directory')
        if d:
            return d
    except Exception:
        pass
    # Default matches image_extractor._cache_dir()
    return str(_ROOT / '.cache' / 'extracted_images')


# ── CLIP ──────────────────────────────────────────────────────────────────────

_clip_model      = None
_clip_preprocess = None
_clip_device     = None


def load_clip():
    global _clip_model, _clip_preprocess, _clip_device
    if _clip_model is not None:
        return
    print("Loading CLIP model (ViT-B/32) — first run downloads ~600 MB …")
    import torch
    import clip
    _clip_device = 'cuda' if torch.cuda.is_available() else 'cpu'
    _clip_model, _clip_preprocess = clip.load('ViT-B/32', device=_clip_device)
    print(f"  CLIP loaded on {_clip_device.upper()}.")


def embed_pil_images(pil_images: list) -> list[list[float]]:
    """Return a list of normalised 512-dim vectors, one per image."""
    import torch
    tensors = []
    for img in pil_images:
        try:
            tensors.append(_clip_preprocess(img.convert('RGB')))
        except Exception:
            # Broken image — use a zero vector so the batch size stays aligned
            import numpy as np
            tensors.append(torch.zeros(3, 224, 224))

    batch = torch.stack(tensors).to(_clip_device)
    with torch.no_grad():
        emb = _clip_model.encode_image(batch).float()
        emb = emb / emb.norm(dim=-1, keepdim=True)
    return emb.cpu().numpy().tolist()


# ── LanceDB ───────────────────────────────────────────────────────────────────

def ensure_table(store):
    if store.exists():
        print(f"Table '{store.table_name}' exists ({store.count()} rows).")
    else:
        print(f"Table '{store.table_name}' will be created on first upsert (dim={CLIP_DIM}, cosine).")


def upsert_batch(store, ids: list, vectors: list, payloads: list):
    store.upsert_batch(ids, vectors, payloads)


# ── WikiArt ───────────────────────────────────────────────────────────────────

def index_wikiart(store, args, progress: ProgressDB):
    """Download and index WikiArt via HuggingFace Datasets."""
    try:
        from datasets import load_dataset
    except ImportError:
        print("ERROR: 'datasets' not installed. Run: pip install datasets", file=sys.stderr)
        sys.exit(1)

    try:
        from tqdm import tqdm
    except ImportError:
        tqdm = None

    hf_cache = os.path.join(args.cache_dir, 'art_datasets', 'wikiart')
    os.makedirs(hf_cache, exist_ok=True)

    # Resolve HuggingFace token: CLI arg > env var > settings.db
    hf_token = args.hf_token or os.environ.get('HF_TOKEN') or None
    if not hf_token:
        try:
            from core.settings import settings
            hf_token = settings.get('huggingface:access_token') or None
        except Exception:
            pass

    print(f"\n{'='*60}")
    print("WikiArt — downloading via HuggingFace (huggan/wikiart)")
    print(f"Cache: {hf_cache}")
    print(f"HF token: {'set' if hf_token else 'not set (fine for public datasets)'}")
    print(f"{'='*60}")
    print("NOTE: First run downloads ~50 GB. Subsequent runs use cache.")
    print()

    ds = load_dataset(
        'huggan/wikiart',
        split='train',
        cache_dir=hf_cache,
        trust_remote_code=True,
        token=hf_token,
    )

    # Show available fields so user knows what we're working with
    print(f"Dataset fields: {list(ds.features.keys())}")

    # Resolve ClassLabel → string for artist field
    features = ds.features
    def _resolve(field, value):
        if field in features and hasattr(features[field], 'int2str'):
            try:
                return features[field].int2str(value)
            except Exception:
                return str(value)
        return str(value) if value is not None else ''

    def _fmt_artist(raw: str) -> str:
        """Convert 'childe-hassam' → 'Childe Hassam'."""
        if not raw or raw == 'Unknown Artist':
            return raw
        return ' '.join(w.capitalize() for w in raw.replace('-', ' ').split())

    total    = min(len(ds), args.limit) if args.limit else len(ds)
    skipped  = 0
    indexed  = 0
    batch_ids, batch_imgs, batch_payloads = [], [], []

    iterator = range(total)
    if tqdm:
        iterator = tqdm(iterator, desc='WikiArt', unit='img', total=total)

    for i in iterator:
        row = ds[i]

        artist = _fmt_artist(_resolve('artist', row.get('artist')))
        # huggan/wikiart has no per-painting titles — use style as a genre label
        title  = row.get('painting') or _resolve('style', row.get('style')) or f"work_{i}"
        if isinstance(title, int):
            title = _resolve('style', title)

        pid = point_id('wikiart', f"{artist}:{title}:{i}")

        if progress.is_done(pid):
            skipped += 1
            continue

        img = row.get('image')
        if img is None:
            continue

        batch_ids.append(pid)
        batch_imgs.append(img)
        batch_payloads.append({
            'artist':     artist,
            'title':      title,
            'source_url': '',
            'dataset':    'wikiart',
            'style':      _resolve('style', row.get('style')),
            'genre':      _resolve('genre', row.get('genre')),
        })

        if len(batch_ids) >= args.batch:
            vectors = embed_pil_images(batch_imgs)
            upsert_batch(store, batch_ids, vectors, batch_payloads)
            for pid_ in batch_ids:
                progress.mark_done(pid_, 'wikiart')
            indexed += len(batch_ids)
            batch_ids, batch_imgs, batch_payloads = [], [], []

    # Flush remainder
    if batch_ids:
        vectors = embed_pil_images(batch_imgs)
        upsert_batch(store, batch_ids, vectors, batch_payloads)
        for pid_ in batch_ids:
            progress.mark_done(pid_, 'wikiart')
        indexed += len(batch_ids)

    print(f"\nWikiArt done. Indexed: {indexed}, Skipped (already done): {skipped}")


# ── MET Open Access ───────────────────────────────────────────────────────────

def _download_met_csv(met_dir: str) -> str:
    import requests
    csv_path = os.path.join(met_dir, 'MetObjects.csv')
    if os.path.exists(csv_path):
        print(f"MET CSV already downloaded: {csv_path}")
        return csv_path
    print(f"Downloading MET open access CSV (~50 MB) …")
    os.makedirs(met_dir, exist_ok=True)
    r = requests.get(MET_CSV_URL, stream=True, timeout=60)
    r.raise_for_status()
    with open(csv_path, 'wb') as f:
        for chunk in r.iter_content(chunk_size=65536):
            f.write(chunk)
    print(f"  Saved to {csv_path}")
    return csv_path


def _load_met_rows(csv_path: str, limit: int | None) -> list[dict]:
    """Filter CSV to public-domain rows with an Object ID.

    NOTE: The MET CSV no longer includes Primary Image / Primary Image Small
    columns (removed ~2023). Image URLs are fetched on demand from the MET
    collection API during the download phase and cached in the progress DB.
    """
    rows = []
    with open(csv_path, encoding='utf-8-sig', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('Is Public Domain', '').strip() != 'True':
                continue
            object_id = row.get('Object ID', '').strip()
            if not object_id:
                continue
            rows.append({
                'object_id':  object_id,
                'artist':     row.get('Artist Display Name', '').strip(),
                'title':      row.get('Title', '').strip(),
                'object_url': row.get('Link Resource', '').strip(),
                'image_url':  '',   # resolved at download time via MET API
            })
            if limit and len(rows) >= limit:
                break
    return rows


_MET_API = 'https://collectionapi.metmuseum.org/public/collection/v1/objects/{}'


def _resolve_met_image_url(object_id: str, progress: 'ProgressDB') -> str:
    """Return the primaryImageSmall URL for a MET object, using the cache."""
    import requests
    cached = progress.get_met_image_url(object_id)
    if cached is not None:
        return cached
    try:
        r = requests.get(_MET_API.format(object_id), timeout=15)
        r.raise_for_status()
        data = r.json()
        url = data.get('primaryImageSmall') or data.get('primaryImage') or ''
    except Exception:
        url = ''
    progress.set_met_image_url(object_id, url)
    return url


def _download_image(row: dict, images_dir: str, progress: 'ProgressDB') -> str | None:
    """Download one MET image. Returns local path or None on failure."""
    import requests
    obj_id   = row['object_id']
    img_path = os.path.join(images_dir, f"{obj_id}.jpg")
    if os.path.exists(img_path) and os.path.getsize(img_path) > 0:
        return img_path
    image_url = row.get('image_url') or _resolve_met_image_url(obj_id, progress)
    if not image_url:
        return None
    try:
        r = requests.get(image_url, timeout=30)
        r.raise_for_status()
        with open(img_path, 'wb') as f:
            f.write(r.content)
        return img_path
    except Exception:
        return None


def index_met(store, args, progress: ProgressDB):
    """Download and index MET Open Access dataset."""
    try:
        from tqdm import tqdm
    except ImportError:
        tqdm = None

    import requests
    from PIL import Image

    met_dir    = os.path.join(args.cache_dir, 'art_datasets', 'met')
    images_dir = os.path.join(met_dir, 'images')
    os.makedirs(images_dir, exist_ok=True)

    print(f"\n{'='*60}")
    print("MET Open Access — downloading CSV + images")
    print(f"Cache: {met_dir}")
    print(f"{'='*60}")

    csv_path = _download_met_csv(met_dir)
    rows     = _load_met_rows(csv_path, args.limit)
    print(f"Eligible MET rows (public domain + image): {len(rows)}")

    # ── Phase 1: download images concurrently ────────────────────────────────
    to_download = [r for r in rows if not os.path.exists(os.path.join(images_dir, f"{r['object_id']}.jpg"))]
    if to_download:
        print(f"Downloading {len(to_download)} images ({MET_WORKERS} workers) …")
        errors = 0
        done   = 0
        dl_iter = to_download
        if tqdm:
            dl_iter = tqdm(to_download, desc='MET download', unit='img')
        with ThreadPoolExecutor(max_workers=MET_WORKERS) as pool:
            futures = {
                pool.submit(_download_image, row, images_dir, progress): row
                for row in to_download
            }
            for fut in as_completed(futures):
                result = fut.result()
                if result is None:
                    errors += 1
                else:
                    done += 1
                if tqdm:
                    dl_iter.update(1)
        print(f"  Downloaded: {done}, Failed: {errors}")
    else:
        print("All MET images already on disk.")

    # ── Phase 2: embed + upsert ───────────────────────────────────────────────
    print("Embedding MET images …")
    skipped = 0
    indexed = 0
    batch_ids, batch_imgs, batch_payloads = [], [], []

    row_iter = rows
    if tqdm:
        row_iter = tqdm(rows, desc='MET embed', unit='img')

    for row in row_iter:
        pid = point_id('met', row['object_id'])
        if progress.is_done(pid):
            skipped += 1
            continue

        img_path = os.path.join(images_dir, f"{row['object_id']}.jpg")
        if not os.path.exists(img_path) or os.path.getsize(img_path) == 0:
            continue  # download failed — skip

        try:
            img = Image.open(img_path)
        except Exception:
            continue

        batch_ids.append(pid)
        batch_imgs.append(img)
        batch_payloads.append({
            'artist':     row['artist'],
            'title':      row['title'],
            'source_url': row['object_url'],
            'dataset':    'met',
        })

        if len(batch_ids) >= args.batch:
            vectors = embed_pil_images(batch_imgs)
            upsert_batch(store, batch_ids, vectors, batch_payloads)
            for pid_ in batch_ids:
                progress.mark_done(pid_, 'met')
            indexed += len(batch_ids)
            batch_ids, batch_imgs, batch_payloads = [], [], []

    # Flush remainder
    if batch_ids:
        vectors = embed_pil_images(batch_imgs)
        upsert_batch(store, batch_ids, vectors, batch_payloads)
        for pid_ in batch_ids:
            progress.mark_done(pid_, 'met')
        indexed += len(batch_ids)

    print(f"\nMET done. Indexed: {indexed}, Skipped (already done): {skipped}")


# ── Verify ────────────────────────────────────────────────────────────────────

def verify(store, args):
    """
    Spot-check the index by querying a handful of known artworks.
    Loads test images from the already-downloaded MET cache and prints the
    top match from LanceDB so you can eyeball quality.
    """
    from PIL import Image
    import random

    images_dir = os.path.join(args.cache_dir, 'art_datasets', 'met', 'images')
    if not os.path.isdir(images_dir):
        print("No MET images found — run indexing first.")
        return

    sample = random.sample(
        [f for f in os.listdir(images_dir) if f.endswith('.jpg')],
        min(5, len(os.listdir(images_dir)))
    )

    load_clip()
    print(f"\nVerifying {len(sample)} random images against '{store.table_name}' …\n")

    for fname in sample:
        img_path = os.path.join(images_dir, fname)
        try:
            img     = Image.open(img_path)
            vectors = embed_pil_images([img])
            hits    = store.search(vectors[0], top_k=1)
            if hits:
                p = hits[0]
                print(
                    f"  {fname:20s}  score={p['score']:.3f}  "
                    f"{p.get('artist','?')} — {p.get('title','?')}  "
                    f"[{p.get('dataset','?')}]"
                )
            else:
                print(f"  {fname:20s}  no results")
        except Exception as e:
            print(f"  {fname:20s}  error: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Build the DocVault local CLIP art index in LanceDB.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--source', choices=['wikiart', 'met', 'all'], default='all',
        help='Which dataset(s) to index (default: all)',
    )
    parser.add_argument(
        '--cache-dir', default=None,
        help='Override the DocVault cache directory for dataset storage',
    )
    parser.add_argument(
        '--table', default=TABLE_NAME,
        help=f'LanceDB table name (default: {TABLE_NAME})',
    )
    parser.add_argument(
        '--batch', type=int, default=64,
        help='CLIP embedding batch size (default: 64; reduce to 32 if OOM)',
    )
    parser.add_argument(
        '--limit', type=int, default=None,
        help='Cap items per source — useful for smoke-testing',
    )
    parser.add_argument(
        '--hf-token', default=None,
        help=(
            'HuggingFace access token (overrides HF_TOKEN env var). '
            'Not required for huggan/wikiart but prevents auth prompts '
            'if the dataset ever becomes gated.'
        ),
    )
    parser.add_argument(
        '--verify', action='store_true',
        help='Spot-check the index with random images instead of building',
    )
    args = parser.parse_args()

    # ── Resolve config ────────────────────────────────────────────────────────
    if args.cache_dir is None:
        args.cache_dir = _read_cache_dir()

    from embeddings.art_vector_store import ArtVectorStore
    store = ArtVectorStore(args.table)

    print(f"DocVault cache dir : {args.cache_dir}")
    print(f"LanceDB table      : {args.table}  (lancedb_storage/)")
    if args.limit:
        print(f"Limit              : {args.limit} items per source  (smoke-test mode)")

    # ── Verify mode ───────────────────────────────────────────────────────────
    if args.verify:
        load_clip()
        verify(store, args)
        return

    # ── Build mode ────────────────────────────────────────────────────────────
    ensure_table(store)
    load_clip()

    progress_path = os.path.join(args.cache_dir, 'art_datasets', '.index_progress.db')
    os.makedirs(os.path.dirname(progress_path), exist_ok=True)
    progress = ProgressDB(progress_path)

    print(f"\nProgress DB        : {progress_path}")
    print(f"Already indexed    : {progress.count()} points total")

    t_start = time.monotonic()

    if args.source in ('wikiart', 'all'):
        index_wikiart(store, args, progress)

    if args.source in ('met', 'all'):
        index_met(store, args, progress)

    elapsed = time.monotonic() - t_start
    total   = progress.count()

    print(f"\n{'='*60}")
    print(f"Done in {elapsed/60:.1f} min")
    print(f"Progress DB total  : {total} indexed")
    print(f"LanceDB row count  : {store.count()}")
    print(f"{'='*60}")
    print(f"\nDownloaded data is in: {os.path.join(args.cache_dir, 'art_datasets')}")
    print("The art_index is ready. The enrichment worker will use it automatically.")

    progress.close()


if __name__ == '__main__':
    main()
