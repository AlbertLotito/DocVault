# DocVault Vision Intelligence — Research Notes

*Last updated: 2026-03-05*

---

## Overview

DocVault's vision intelligence covers two related but distinct problems:

1. **General image description** — describing arbitrary images found inside documents (PDFs, archives, web pages). Handled by `extractors/vision.py` via a local Ollama multimodal model.
2. **Art identification** — identifying specific paintings, prints, and artworks in a user's collection. Handled by a two-tier pipeline: local CLIP similarity search + cloud API fallback.

This document covers both, with emphasis on the art identification system where most of the complexity lives.

---

## Part 1 — General Image Description (`vision.py`)

### Role

`extractors/vision.py` is a shared utility called by multiple extractors (PDF image harvester, standalone image extractor, face analysis pipeline). It sends a PIL image to the configured Ollama vision model and returns a text description.

### Configuration

| Setting | Default | Notes |
|---------|---------|-------|
| `vision:model` | `minicpm-v` | Must support image input. Alternatives: `llava`, `llava-llama3` |
| `vision:describe_images` | `true` | Master on/off switch |
| `ollama:max_parallel` | `1` | Semaphore limit for all Ollama callers |

### Guard Filters

Before any Ollama call, two checks run:

- **Minimum dimension**: images smaller than 64px in either dimension are skipped. Thumbnails, icons, and decorative spacers produce noise, not descriptions.
- **Aspect ratio**: images with a long/short ratio above 10:1 are skipped. Thin ruled lines, dividers, and banner strips waste model slots.

### Ollama Governor

All Ollama calls (vision, embedding, LLM chat) pass through `core/monitor.py:ollama_governor()`. This is a `threading.Semaphore` gated by `ollama:max_parallel`. With the default of 1, only one thread can call Ollama at a time across the entire application — extraction worker, embedding worker, and art enrichment worker all compete for the same slot.

Under thermal pressure (CPU/GPU throttled state), the governor additionally forces calls through a serial lock, further reducing throughput.

### "No Slots Available" — Discovered Limitation

**Symptom:** `[vision] Unexpected error: no slots available after 10 retries (status code: 500)`

**Cause:** Ollama returns HTTP 500 "no slots available" when it is busy (model loading, VRAM swap between models, or concurrent external callers). The `ollama` Python client internally retries 10 times before raising the exception. By the time the exception surfaces, ~30–60 seconds have passed silently.

The semaphore prevents DocVault from sending concurrent requests, but does not protect against Ollama being busy from external causes (other apps, model loading latency) or from DocVault's own model-switching overhead (vision model ↔ embedding model ↔ chat model all compete for VRAM).

**Fix implemented:** `vision.py` now wraps the Ollama call in a retry loop with exponential backoff (10s → 20s → 40s → 60s, 4 retries). The exception is caught specifically on `"no slots"` in the message; other errors (connection refused, 404 model not found, CUDA error) are handled separately and do not retry. `embedder.py` has the same fix.

**Remaining exposure:** If Ollama is persistently unavailable (server not running, model not pulled), the retry loop will exhaust and return empty string / None after ~130 seconds. This is intentional — better to delay and succeed than to fail-fast and lose the extraction.

---

## Part 2 — Art Identification Pipeline

### Design Philosophy

The art collection problem is different from general image description:

- The user already knows these are artworks. We want *which* artwork, not a description.
- The collection may be large (tens of thousands of images), so the system must be fully automatic and unattended.
- Cloud API calls cost money. A local first-pass filter avoids paying for obvious negatives.
- Many artworks are scans of print reproductions, not museum-quality photographs. Image quality is variable.
- Filenames are often meaningless (`img158.jpg`, `00234.bmp`). No EXIF. No embedded metadata.

### Pipeline Architecture

```
Image
  │
  ▼
┌─────────────────────────────────────┐
│  Tier 1: CLIP Local (free, instant) │
│  openai/clip ViT-B/32               │
│  Qdrant art_index collection        │
└─────────────────────────────────────┘
       │
       ├─ score ≥ 0.80 AND named artist → ACCEPT (return immediately, no cloud call)
       │
       ├─ score ≥ 0.80 but "Unknown Artist" → FALLTHROUGH to cloud
       │
       ├─ score 0.70–0.79 → keep as candidate, FALLTHROUGH to cloud
       │
       └─ score < 0.70 → discard, FALLTHROUGH to cloud
               │
               ▼
┌─────────────────────────────────────────────────────┐
│  Tier 2: Cloud (costs money, slower)                │
│  Primary: Google Vision WEB_DETECTION               │
│  Fallback: Bing Visual Search                       │
│  Monthly caps enforced from settings                │
└─────────────────────────────────────────────────────┘
       │
       ├─ cloud result with title → return cloud result (if confidence ≥ CLIP candidate)
       │
       └─ cloud returns only generic labels → return CLIP candidate (if any)
```

Results are written to `.nfo` sidecar files next to each image (INI format, human-readable). The enrichment worker runs as a low-priority background daemon, sleeping 300s between vault scans.

---

## Part 3 — CLIP Local Search

### What CLIP Does

CLIP (Contrastive Language-Image Pretraining, OpenAI, ViT-B/32) encodes images into 512-dimensional vectors in a shared image-text embedding space. Two images that look similar will have high cosine similarity. Two images from the same artistic style/period will cluster together even if they depict different subjects.

The art index stores CLIP embeddings of known artworks. Given a query image, we find the nearest neighbours in the index.

### The art_index Collection

The Qdrant `art_index` collection holds pre-computed CLIP embeddings with payload:

```json
{
  "artist": "Paul Gauguin",
  "title": "Post_Impressionism",
  "style": "Post_Impressionism",
  "genre": "landscape",
  "source_url": "",
  "dataset": "wikiart"
}
```

Current state of the index:

| Dataset | Entries | Metadata quality |
|---------|---------|-----------------|
| `huggan/wikiart` (HuggingFace) | ~81,444 | Style/genre only — **no individual painting titles** |
| MET Open Access | indexing | Artist + title + year + museum — full metadata |

### huggan/wikiart — Critical Limitation

The `huggan/wikiart` dataset on HuggingFace is a **classification training dataset**, not a crawl of WikiArt.org. Key properties:

- Labels are `artist` (ClassLabel), `style` (ClassLabel), `genre` (ClassLabel) — categorical integers resolved to strings.
- The `artist` field uses hyphenated lowercase slugs (`childe-hassam`, `pablo-picasso`) that must be converted to display names.
- **There are no individual painting titles.** The `title` field in our index is populated with the style name (`Impressionism`, `Post_Impressionism`, `Realism`, etc.) as a fallback, because no title data exists in the dataset.
- Coverage is sparse for many artists. Paul Gauguin has only 5 entries in our 81k index. Many prominent artists have zero entries.
- WikiArt.org itself has ~250,000+ paintings with full artist/title/year/museum metadata. The HuggingFace dataset captures perhaps 30% of that.

**Practical consequence:** CLIP will often find a high-confidence match (e.g. 0.87–0.88) to a record with `artist: "Unknown Artist", title: "Realism"`. This is visually correct (the algorithm found paintings that look similar) but metadata-useless. The system detects this and falls through to cloud.

**The "Unknown Artist" rule:** If CLIP's top match has artist = "Unknown Artist" (or empty), the result is never accepted regardless of confidence. Cloud is always attempted. This was added after observing that a score of 0.87 on an "Unknown Artist / Realism" record was firing as an accepted result, suppressing the Google Vision call that would have provided the actual title.

### Score Ranges Observed in Testing

| Score range | Observed meaning |
|-------------|-----------------|
| 0.92+ | Typically the exact painting, or a very close variant/study |
| 0.85–0.92 | Same artist, same period, similar subject — strong genre match |
| 0.80–0.85 | Same style movement, different artist — useful for classification |
| 0.70–0.80 | Related movement, noisy — worth checking cloud |
| < 0.70 | Weak or spurious — discard |

The accept threshold (0.80) and fallback threshold (0.70) are configurable in settings (`art:clip_accept_threshold`, `art:clip_fallback_threshold`).

---

## Part 4 — Google Vision WEB_DETECTION

### What It Does

Google Vision's `WEB_DETECTION` feature analyses an image and returns:

- **Web entities** — named concepts or specific artworks identified by Google's Knowledge Graph, with confidence scores.
- **Pages with matching images** — web pages where this image (or visually similar ones) appears, with page titles and URLs.
- **Full matching images** — exact or near-exact copies of the image found on the web.
- **Visually similar images** — images that look like ours (same style, composition).
- **Best guess labels** — short descriptive labels Google assigns to the overall image.

### API Configuration

```python
payload = {
    "requests": [{
        "image": {"content": base64_encoded_image},
        "features": [{"type": "WEB_DETECTION", "maxResults": 10}]
    }]
}
url = "https://vision.googleapis.com/v1/images:annotate?key={api_key}"
```

**Critical: `maxResults` must be ≥ 10.** In testing, a specific high-scoring entity (`"Robe violette et Anémones"`, score 1.05) only appeared in the results when requesting 10+ entities. With `maxResults: 5`, it was absent and the generic entity `"Painting"` (score 0.95) was returned instead. The API does not simply return the top-N by score — the selection algorithm is opaque.

**Encoding:** The API returns valid UTF-8 JSON. Use `json.loads(resp.content.decode('utf-8'))` rather than `resp.json()` to ensure correct decoding. The entity description `"Robe violette et Anémones"` was verified to contain the correct Unicode codepoint `U+00E9` (é) after decoding.

### Entity Extraction Strategy

Entities are sorted by score, descending. The first entity whose description is not in the generic-labels filter is used as the title:

```python
_generic_labels = {
    'painting', 'art', 'artwork', 'drawing', 'watercolor painting',
    'oil painting', 'oil painting reproduction', 'illustration',
    'sketch', 'print', 'artist', 'sculptor', 'expressionism',
    'abstract art', 'surrealism', 'impressionism', 'post-impressionism',
}
```

If the entity description contains ` - `, it is split as `artist - title`. Otherwise the whole description is used as the title.

### Discovered Limitation: Artist Attribution is Unreliable

**The problem:** `pagesWithMatchingImages` lists web pages that contain our image. Intuitively, if a page about Paul Gauguin contains our image, that page title should give us the artist. In practice, this is unreliable because art websites (Arthive, artchive.ru, museum sites) include our image in "visually similar works" sections on pages about *different* artists.

**Observed behaviour — Gauguin test case:**
The painting `img158.jpg` (Paul Gauguin, *Breton Peasant Woman with Cows*, c. 1890) was present on:
- 3 Arthive pages for **Wassily Kandinsky** (appearing as "similar style" examples)
- 1 Arthive page for **Pablo Picasso**
- 1 artchive.ru page for **Henri Matisse**
- 1 Pinterest page titled `"img158 – Paul Gauguin"` ← correct

A consensus vote (≥ 2 mentions) would incorrectly assign **Kandinsky** as the artist. Even the Pinterest signal (single mention of Gauguin) was drowned out. Any page-title-based artist extraction will produce wrong answers on this class of image.

**Resolution:** Artist attribution from `pagesWithMatchingImages` was removed entirely. The API is used only for the entity-name title. Artist is left empty (`''`) from the Google Vision tier. The `.nfo` file will have a title but no artist when cloud is the only source.

**Why the entity name is more trustworthy than page titles:**
The entity score for `"Robe violette et Anémones"` (1.05) reflects Google's confidence that the image depicts a specific known artwork. The entity ID (`/m/02q9w6c`) is a Knowledge Graph node for that specific painting. Even though the entity identified the wrong painting (a Matisse rather than the Gauguin), it correctly identified *a specific artwork in the right artistic family*, rather than attaching the wrong artist through noisy page parsing.

### Entity Accuracy — Similarity vs. Identity

WEB_DETECTION performs **visual similarity matching**, not true reverse image lookup. The returned entity is the closest artwork in Google's indexed corpus, not necessarily the exact painting.

**Test case — Gauguin `img158.jpg`:**
- Actual painting: *Breton Peasant Woman with Cows*, Paul Gauguin, c. 1890
- Google Vision entity: `"Robe violette et Anémones"` — Henri Matisse, 1937 (a different painting entirely)
- The two paintings are stylistically distant (Gauguin Breton realism vs. Matisse still life), but Google's web-indexed representation of Gauguin's painting may be sparse, leading to a match with a better-indexed Matisse work

Google's web-based **Image Search** (the browser product) correctly identified the painting and found it on WikiArt.org. This confirms the painting is indexed, but the API and the product use different matching pipelines.

### Cost and Rate Limiting

Google Vision API billing applies per request after the free tier (1,000 requests/month). DocVault tracks monthly usage in settings.db:

- Keys: `art:_cost_month_google` (YYYY-MM), `art:_cost_count_google`
- Auto-resets when the month changes
- Configurable monthly cap: `art:google_monthly_limit` (default 1,000)
- When the cap is reached, the Google tier is skipped; Bing fallback is tried if configured

---

## Part 5 — Bing Visual Search

Bing Visual Search (`POST https://api.bing.microsoft.com/v7.0/images/visualsearch`) takes a multipart form upload with the image and returns structured results including best representative query, image tags, and related pages.

DocVault uses it as a secondary fallback when:
- Google is not configured
- Google returns a low-confidence result (< `art:cloud_fallback_threshold`, default 0.70)
- Google's monthly limit is reached

A result is returned at nominal confidence 0.75 when Bing returns a `bestRepresentativeQuery`. Monthly cap tracked at `art:bing_monthly_limit`.

Bing Visual Search has not been extensively tested in production (no Bing API key configured during development). The architecture mirrors Google but results quality is unknown for art use cases.

---

## Part 6 — Art Index Datasets

### build_art_index.py

`tools/build_art_index.py` is a standalone CLI for seeding the `art_index` Qdrant collection. It is **not** run automatically — it is run once (or when you want to expand coverage) from the command line.

```bash
# Seed from both sources
python tools/build_art_index.py --source all

# WikiArt only (fast, already done)
python tools/build_art_index.py --source wikiart

# MET only (slow — downloads ~115k images)
python tools/build_art_index.py --source met

# Verify results
python tools/build_art_index.py --verify
```

Progress is tracked in `.cache/extracted_images/.index_progress.db` (SQLite) with deterministic UUID5 point IDs, so interrupted runs resume without re-embedding already-indexed images.

### Dataset Comparison

| Dataset | Images | Artist quality | Title quality | Coverage |
|---------|--------|---------------|---------------|----------|
| huggan/wikiart (HuggingFace) | ~81,444 | Good for major artists; sparse/missing for many | **None** — style name used as title | ~30% of WikiArt.org |
| MET Open Access | ~115,000 | Excellent — structured museum records | Excellent — official titles | Western art, antiquities, decorative arts |
| WikiArt.org (full) | ~250,000+ | Excellent | Excellent | Broadest art history coverage |

**WikiArt.org full scrape** was considered but deferred. The site does not have a public API. Scraping at scale would impose significant load on a community-maintained resource. The MET dataset provides better metadata for museum-catalogued works, and its images are officially in the public domain.

### Expected Improvement from MET Data

MET records look like:

```json
{
  "artist": "Paul Gauguin",
  "title": "Two Tahitian Women",
  "style": "",
  "dataset": "met",
  "source_url": "https://www.metmuseum.org/art/collection/search/438821"
}
```

When a painting matches a MET record at high CLIP confidence, the artist and title will both be correct and complete. The MET has strong coverage of Impressionism, Post-Impressionism, and 19th–20th century Western art — exactly the period where WikiArt's classification dataset is weakest.

---

## Part 7 — The .nfo Sidecar Format

Results are written as INI-style sidecar files:

```ini
[artwork]
artist = Paul Gauguin
title = Two Tahitian Women
confidence = 0.923
source = clip
source_url = https://www.metmuseum.org/art/collection/search/438821
identified_at = 2026-03-05T10:14:32.441Z
```

`source` is one of `clip`, `google`, or `bing`. The enrichment worker skips any image that already has a `.nfo` file (identified previously). To re-identify, delete the `.nfo`.

`.nfo` files are added to the vault scanner's ignore list so they are never ingested as documents.

---

## Part 8 — Known Gaps and Future Directions

### Immediate gaps

| Gap | Impact | Effort |
|-----|--------|--------|
| WikiArt dataset has no titles | CLIP always returns style names, not painting names | High — requires different data source |
| Google Vision returns wrong entity for some paintings | Title is in the right family but not exact | Medium — better parsing or alternative API |
| Artist field always empty from Google Vision | NFO files have title but no artist attribution | Medium — requires structured lookup |
| MET indexing not complete | Missing ~115k high-quality paintings | Low — just time |
| No Bing test data | Bing fallback quality unknown | Low |

### Potential improvements

**Google Knowledge Graph API:** When Google Vision returns an entity with a Freebase ID (e.g. `/m/02q9w6c`), the Knowledge Graph Search API can look up structured data for that entity including `name`, `description`, `detailedDescription`, and crucially the artist. One additional API call per image would give us artist attribution from entities. Requires a separate API key.

**Structured entity filtering:** Rather than picking the highest-scoring entity and filtering generic labels, we could filter specifically for entities that are known artwork types (paintings, drawings, prints) by checking the entity ID against a type list. This would skip entities like "Expressionism" (a movement, not a work) more reliably than the current string-match filter.

**Score thresholds need calibration:** The 0.80 accept threshold was set conservatively. After the MET data is fully indexed, a sample of known paintings should be tested to determine what CLIP score corresponds to a correct exact match vs. a same-artist match vs. a same-style match. The thresholds should be tuned based on actual precision/recall data.

**Reverse image lookup alternative:** Google's reverse image search (the web product) finds the exact painting on WikiArt.org while the Vision API does not. The API and product use different pipelines. A possible alternative is to use a headless browser (Playwright) to submit the image to Google Images and scrape the result. This is not an API contract and may break, but would provide true reverse-image identification rather than similarity matching.

**Local model alternative to CLIP:** CLIP ViT-B/32 is a general-purpose model trained on noisy internet image-text pairs. A model fine-tuned specifically on artwork — or a larger CLIP variant (ViT-L/14) — would produce better embeddings for this use case. The trade-off is model size and embedding time.

---

## Appendix — Settings Reference

| Key | Group | Default | Description |
|-----|-------|---------|-------------|
| `art:clip_enabled` | art | `true` | Enable local CLIP tier |
| `art:clip_qdrant_collection` | art | `art_index` | Qdrant collection for art embeddings |
| `art:clip_accept_threshold` | art | `0.80` | Min CLIP score to accept without cloud (named artist required) |
| `art:clip_fallback_threshold` | art | `0.70` | Min CLIP score to keep as candidate |
| `art:cloud_provider` | art | `google` | Primary cloud provider (`google` or `bing`) |
| `art:cloud_fallback_threshold` | art | `0.70` | Min cloud confidence to prefer over CLIP |
| `art:google_monthly_limit` | google | `1000` | Max Google Vision calls per calendar month |
| `art:bing_monthly_limit` | bing | `1000` | Max Bing Visual Search calls per calendar month |
| `google:vision_api_key` | google | `` | Google Cloud Vision API key (billing must be enabled) |
| `bing:visual_search_api_key` | bing | `` | Bing Visual Search subscription key |
| `vision:model` | vision | `minicpm-v` | Ollama model for image description |
| `vision:describe_images` | vision | `true` | Enable/disable Ollama image description |
| `ollama:max_parallel` | ollama | `1` | Max concurrent Ollama requests across all workers |
