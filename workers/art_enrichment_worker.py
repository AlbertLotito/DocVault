"""
Art Enrichment Worker — idle daemon.

Identification pipeline (per image):

  Tier 1 — Local CLIP + LanceDB Art Index
    Zero cost, fully private. Requires a pre-built 'art_index' LanceDB
    table (WikiArt / MET / similar). If the table is absent or
    CLIP is not installed, this tier is silently skipped.

    - confidence >= art:clip_accept_threshold  → accept, skip cloud
    - confidence >= art:clip_fallback_threshold → write result, cloud optional
    - confidence <  art:clip_fallback_threshold → fall through to cloud

  Tier 2 — Cloud (Google Vision / Bing Visual Search)
    Configurable primary provider (art:cloud_provider = google | bing).
    If primary returns confidence < art:cloud_fallback_threshold AND the
    secondary provider is configured, the secondary is tried automatically.
    Monthly call limits per provider are enforced (art:google_monthly_limit,
    art:bing_monthly_limit). When a limit is reached, that provider is
    skipped for the rest of the calendar month.
    If no cloud API keys are configured, cloud tier is silently skipped.

State tracking: .nfo sidecar per image (INI format, [artwork] section).
  - No .nfo  → unprocessed
  - .nfo exists with renamed_to=(failed) → eligible for retry after N hours
  - .nfo exists with any other renamed_to → done
"""

import os
import time
import json
import threading
import configparser
import requests
import base64
import re
from datetime import datetime, timezone, timedelta
from core import logger, manager
from workers.utils import should_pause_or_throttle

_IMAGE_EXTS = frozenset({
    '.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp', '.gif'
})

# Filesystem chars illegal on Windows (and generally unsafe cross-platform)
_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')


# ── .nfo helpers ──────────────────────────────────────────────────────────────

def _nfo_path(image_path: str) -> str:
    return image_path + '.nfo'


def _read_nfo(image_path: str) -> dict:
    """Read a .nfo sidecar. Returns {} if absent or unreadable."""
    nfo = _nfo_path(image_path)
    if not os.path.exists(nfo):
        return {}
    cfg = configparser.RawConfigParser()
    try:
        cfg.read(nfo, encoding='utf-8')
        return dict(cfg['artwork']) if 'artwork' in cfg else {}
    except Exception:
        return {}


def _write_nfo(image_path: str, data: dict):
    """Write or overwrite a .nfo sidecar with [artwork] section."""
    nfo = _nfo_path(image_path)
    cfg = configparser.RawConfigParser()
    cfg['artwork'] = data
    with open(nfo, 'w', encoding='utf-8') as f:
        cfg.write(f)


# ── Name sanitisation ─────────────────────────────────────────────────────────

def _sanitise(name: str, max_len: int = 100) -> str:
    """Strip illegal filesystem chars and truncate."""
    name = _ILLEGAL_CHARS.sub('', name).strip()
    return name[:max_len] if len(name) > max_len else name


def _build_target_name(artist: str, title: str, ext: str, folder: str) -> str:
    """
    Build <Artist> - <Title>.<ext>, collision-safe within folder.
    Falls back to 'Unnamed' if title is empty.
    """
    artist = _sanitise(artist or 'Unknown Artist')
    title  = _sanitise(title  or 'Unnamed')
    base   = f"{artist} - {title}"
    target = os.path.join(folder, f"{base}{ext}")

    if not os.path.exists(target):
        return target

    # Collision resolution
    i = 2
    while True:
        candidate = os.path.join(folder, f"{base} _({i}){ext}")
        if not os.path.exists(candidate):
            return candidate
        i += 1


# ── Transactional rename ───────────────────────────────────────────────────────

def _transactional_rename(image_path: str, artist: str, title: str,
                           db_path: str) -> tuple[bool, str]:
    """
    Rename image_path to <Artist> - <Title>.<ext>.
    Returns (success, new_path_or_error_message).
    Rolls back on any failure.
    """
    folder   = os.path.dirname(image_path)
    ext      = os.path.splitext(image_path)[1]
    target   = _build_target_name(artist, title, ext, folder)
    renamed  = False

    try:
        # Step 1: Update .nfo with intended name (before rename)
        nfo_data = _read_nfo(image_path)
        nfo_data['renamed_to'] = os.path.basename(target)
        _write_nfo(image_path, nfo_data)

        # Step 2: Rename the image file
        os.rename(image_path, target)
        renamed = True

        # Step 3: Verify
        if not os.path.exists(target):
            raise OSError(f"Target {target} does not exist after rename")
        if os.path.exists(image_path):
            raise OSError(f"Original {image_path} still exists after rename")

        # Step 4: Move the .nfo sidecar to match new name
        old_nfo = _nfo_path(image_path)
        new_nfo = _nfo_path(target)
        if os.path.exists(old_nfo):
            os.rename(old_nfo, new_nfo)

        # Step 5: Update DB path record (same hash, new path)
        try:
            import hashlib
            with open(target, 'rb') as f:
                h = hashlib.sha256()
                for chunk in iter(lambda: f.read(65536), b''):
                    h.update(chunk)
                file_hash = h.hexdigest()

            from core.manager import _connect, get_db_path
            conn_path = db_path or get_db_path()
            with _connect(conn_path) as conn:
                conn.execute(
                    "UPDATE tasks SET file_path = ? WHERE file_hash = ?",
                    (target, file_hash)
                )
                conn.commit()
        except Exception as db_err:
            # DB update failure is non-fatal — vault scan will reconcile
            logger.warn(f"[art-enrich] DB path update failed for {target}: {db_err}", ext="art")

        return True, target

    except Exception as e:
        # ROLLBACK
        if renamed and os.path.exists(target) and not os.path.exists(image_path):
            try:
                os.rename(target, image_path)
            except Exception as rb_err:
                logger.error(f"[art-enrich] ROLLBACK FAILED for {image_path}: {rb_err}", ext="art")

        # Mark .nfo as failed
        try:
            nfo_data = _read_nfo(image_path)
            nfo_data['renamed_to'] = '(failed)'
            _write_nfo(image_path, nfo_data)
        except Exception:
            pass

        return False, str(e)


# ── Queue builder ─────────────────────────────────────────────────────────────

def _find_unprocessed(vault_dirs: list[str]) -> list[str]:
    """Walk vault directories, return image paths with no .nfo sidecar."""
    queue = []
    for vault_dir in vault_dirs:
        if not os.path.isdir(vault_dir):
            continue
        for root, dirs, files in os.walk(vault_dir):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext not in _IMAGE_EXTS:
                    continue
                path = os.path.normpath(os.path.join(root, name))
                if not os.path.exists(_nfo_path(path)):
                    queue.append(path)
    return queue


def _find_retry_eligible(vault_dirs: list[str], retry_hours: int) -> list[str]:
    """Return image paths whose .nfo has renamed_to=(failed) and is old enough to retry."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=retry_hours)
    eligible = []
    for vault_dir in vault_dirs:
        if not os.path.isdir(vault_dir):
            continue
        for root, dirs, files in os.walk(vault_dir):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext not in _IMAGE_EXTS:
                    continue
                path = os.path.normpath(os.path.join(root, name))
                nfo = _nfo_path(path)
                if not os.path.exists(nfo):
                    continue
                data = _read_nfo(path)
                if data.get('renamed_to') != '(failed)':
                    continue
                mtime = datetime.fromtimestamp(os.path.getmtime(nfo), tz=timezone.utc)
                if mtime < cutoff:
                    eligible.append(path)
    return eligible


# ── Token bucket rate limiter ─────────────────────────────────────────────────

class _TokenBucket:
    def __init__(self, rate_per_minute: int):
        self._rate   = max(rate_per_minute, 1)
        self._tokens = float(self._rate)
        self._last   = time.monotonic()
        self._lock   = threading.Lock()

    def consume(self):
        """Block until a token is available."""
        while True:
            with self._lock:
                now     = time.monotonic()
                elapsed = now - self._last
                self._tokens = min(
                    self._rate,
                    self._tokens + elapsed * (self._rate / 60.0)
                )
                self._last = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
            time.sleep(1)


# ── Cost tracking ─────────────────────────────────────────────────────────────

def _current_month() -> str:
    return datetime.now().strftime('%Y-%m')


def _get_cloud_call_count(provider: str) -> int:
    """Return number of cloud calls made this calendar month for provider."""
    month_key = f'art:_cost_month_{provider}'
    count_key = f'art:_cost_count_{provider}'
    stored_month = manager.get_setting(month_key) or ''
    if stored_month != _current_month():
        return 0
    try:
        return int(manager.get_setting(count_key) or 0)
    except (TypeError, ValueError):
        return 0


def _increment_cloud_call(provider: str):
    """Increment the monthly call counter for provider, resetting if the month changed."""
    month_key = f'art:_cost_month_{provider}'
    count_key = f'art:_cost_count_{provider}'
    current = _current_month()
    stored_month = manager.get_setting(month_key) or ''
    if stored_month != current:
        manager.set_setting(month_key, current)
        manager.set_setting(count_key, '1')
    else:
        count = 0
        try:
            count = int(manager.get_setting(count_key) or 0)
        except (TypeError, ValueError):
            pass
        manager.set_setting(count_key, str(count + 1))


# ── Tier 1: Local CLIP ────────────────────────────────────────────────────────

_clip_model      = None
_clip_preprocess = None
_clip_device     = None
_clip_load_tried = False


def _ensure_clip_loaded() -> bool:
    global _clip_model, _clip_preprocess, _clip_device, _clip_load_tried
    if _clip_load_tried:
        return _clip_model is not None
    _clip_load_tried = True
    try:
        import torch
        import clip as _clip
        _clip_device = 'cuda' if torch.cuda.is_available() else 'cpu'
        _clip_model, _clip_preprocess = _clip.load('ViT-B/32', device=_clip_device)
        logger.info("[art-enrich] CLIP model loaded.", ext="art")
        return True
    except ImportError:
        logger.info("[art-enrich] CLIP not installed — Tier 1 disabled.", ext="art")
        return False
    except Exception as e:
        logger.warn(f"[art-enrich] CLIP load failed: {e}", ext="art")
        return False


def _call_clip_local(image_path: str, table_name: str) -> dict | None:
    """
    Generate a CLIP embedding for image_path and query the LanceDB art index.
    Returns a result dict or None if unavailable.
    """
    if not _ensure_clip_loaded():
        return None

    try:
        import torch
        from PIL import Image
        from embeddings.art_vector_store import ArtVectorStore

        # Build CLIP embedding
        pil_img = Image.open(image_path).convert('RGB')
        tensor  = _clip_preprocess(pil_img).unsqueeze(0).to(_clip_device)
        with torch.no_grad():
            emb = _clip_model.encode_image(tensor)
            emb = emb / emb.norm(dim=-1, keepdim=True)
        vec = emb[0].cpu().numpy().tolist()

        store = ArtVectorStore(table_name)
        hits  = store.search(vec, top_k=1)

        if not hits:
            return None

        best = hits[0]
        return {
            'artist':     best.get('artist', ''),
            'title':      best.get('title', ''),
            'confidence': float(best['score']),
            'source_url': best.get('source_url', ''),
            'tier':       'clip',
        }

    except Exception as e:
        logger.warn(f"[art-enrich] CLIP query failed: {e}", ext="art")
        return None


# ── Tier 2: Google Vision ─────────────────────────────────────────────────────

def _call_google_vision(image_path: str, api_key: str) -> dict:
    """
    Call Google Vision API (WEB_DETECTION).
    Returns result dict. Raises requests.HTTPError on API errors.

    Extraction strategy (in priority order):
    1. Pages with fullMatchingImages — exact image hits, most reliable source
    2. All page titles/URLs — parse "by Artist", "Title – Artist", arthive URL slugs
    3. Top web entity name — fallback when pages give nothing useful
    """
    import json as _json

    with open(image_path, 'rb') as f:
        image_b64 = base64.b64encode(f.read()).decode('utf-8')

    payload = {
        "requests": [{
            "image": {"content": image_b64},
            "features": [{"type": "WEB_DETECTION", "maxResults": 10}]
        }]
    }

    url  = f"https://vision.googleapis.com/v1/images:annotate?key={api_key}"
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()

    data = _json.loads(resp.content.decode('utf-8'))
    web  = data.get('responses', [{}])[0].get('webDetection', {})

    entities = web.get('webEntities', [])
    pages    = web.get('pagesWithMatchingImages', [])

    # ── Step 1: top web entity → title (and artist if separated by " - ") ──────
    _generic_labels = {
        'painting', 'art', 'artwork', 'drawing', 'watercolor painting',
        'oil painting', 'oil painting reproduction', 'illustration',
        'sketch', 'print', 'artist', 'sculptor', 'expressionism',
        'abstract art', 'surrealism', 'impressionism', 'post-impressionism',
    }

    # Walk entities from highest score, pick first non-generic description
    best_entity = max(entities, key=lambda e: e.get('score', 0), default={})
    best_confidence = float(best_entity.get('score', 0.0))

    artist, title = '', ''
    for ent in sorted(entities, key=lambda e: e.get('score', 0), reverse=True):
        desc = ent.get('description', '').strip()
        if not desc or desc.lower() in _generic_labels:
            continue
        if ' - ' in desc:
            parts = desc.split(' - ', 1)
            artist, title = parts[0].strip(), parts[1].strip()
        else:
            title = desc
        break

    # ── Step 2: source URL — prefer first exact-match page ───────────────────
    # NOTE: artist is intentionally NOT extracted from page titles.
    # Vision API pages often show our image as "similar works" on pages about
    # different artists, making any page-based attribution highly unreliable.
    source_url = ''
    for p in pages:
        if p.get('fullMatchingImages'):
            source_url = p.get('url', '')
            break
    if not source_url and pages:
        source_url = pages[0].get('url', '')

    return {
        'artist':     artist,
        'title':      title,
        'confidence': best_confidence,
        'source_url': source_url,
        'tier':       'google',
    }


# ── Tier 2: Bing Visual Search ────────────────────────────────────────────────

def _call_bing_visual_search(image_path: str, api_key: str) -> dict:
    """
    Call Bing Visual Search API.
    Returns result dict. Raises requests.HTTPError on API errors.
    """
    with open(image_path, 'rb') as f:
        image_data = f.read()

    headers = {'Ocp-Apim-Subscription-Key': api_key}
    knowledge_request = json.dumps({"imageInfo": {}})
    files = {
        'knowledgeRequest': (None, knowledge_request, 'application/json'),
        'image':            ('image', image_data, 'application/octet-stream'),
    }

    url  = 'https://api.bing.microsoft.com/v7.0/images/visualsearch'
    resp = requests.post(url, headers=headers, files=files, timeout=30)
    resp.raise_for_status()

    data = resp.json()

    artist, title, source_url = '', '', ''
    # Bing returns a confidence-like score in bestRepresentativeQuery
    best_query = data.get('bestRepresentativeQuery', {})
    query_text = best_query.get('text', '')

    # Walk tags for richer entity names and page links
    for tag in data.get('tags', []):
        for action in tag.get('actions', []):
            if action.get('actionType') != 'PagesIncluding':
                continue
            for value in action.get('data', {}).get('value', []):
                name = value.get('name', '')
                if ' - ' in name:
                    parts  = name.split(' - ', 1)
                    artist = parts[0].strip()
                    title  = parts[1].strip()
                elif name:
                    title = name
                source_url = value.get('contentUrl', '')
                break
            if title:
                break
        if title:
            break

    # Fall back to bestRepresentativeQuery text if no tag name found
    if not title and query_text:
        if ' - ' in query_text:
            parts  = query_text.split(' - ', 1)
            artist = parts[0].strip()
            title  = parts[1].strip()
        else:
            title = query_text[:100]

    # Bing doesn't provide an explicit confidence score; use 0.75 as a nominal value
    # when we have a result, 0.0 otherwise.
    confidence = 0.75 if title else 0.0

    return {
        'artist':     artist,
        'title':      title,
        'confidence': confidence,
        'source_url': source_url,
        'tier':       'bing',
    }


# ── Result validation ─────────────────────────────────────────────────────────

def _validate_art_result(result: dict) -> dict | None:
    """Validate and sanitise an identification result dict.

    Ensures all fields are the expected types and within safe bounds.
    Returns a clean copy, or None if result is not a dict.
    """
    if not isinstance(result, dict):
        return None
    try:
        confidence = float(result.get('confidence', 0.0) or 0.0)
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        'artist':     str(result.get('artist',     '') or '')[:200].strip(),
        'title':      str(result.get('title',      '') or '')[:200].strip(),
        'confidence': confidence,
        'source_url': str(result.get('source_url', '') or '')[:500],
        'tier':       str(result.get('tier', 'unknown') or 'unknown')[:50],
    }


# ── Identification orchestrator ───────────────────────────────────────────────

def _identify_artwork(image_path: str) -> dict | None:
    """
    Run the full identification pipeline for one image.

    Returns a result dict with artist/title/confidence/source_url/tier,
    or None if no identification was possible (no CLIP, no cloud config).

    Does NOT write .nfo or rename — caller handles that.
    """
    from core.settings import settings

    # ── Tier 1: Local CLIP ─────────────────────────────────────────────────
    clip_enabled  = str(settings.get('art:clip_enabled') or 'true').lower() == 'true'
    clip_table    = settings.get('art:clip_table') or 'art_index'
    clip_accept   = float(settings.get('art:clip_accept_threshold') or 0.80)
    clip_fallback = float(settings.get('art:clip_fallback_threshold') or 0.70)

    clip_result = None
    if clip_enabled:
        clip_result = _call_clip_local(image_path, clip_table)
        if clip_result:
            conf = clip_result['confidence']
            artist = clip_result.get('artist', '')
            # "Unknown Artist" is not a useful identification — fall through to cloud
            # even if confidence is above the accept threshold
            unknown_artist = (not artist or artist.lower() in ('unknown artist', 'unknown', ''))
            if conf >= clip_accept and not unknown_artist:
                # High confidence and a named artist — accept and skip cloud
                logger.info(
                    f"[art-enrich] CLIP accepted {os.path.basename(image_path)} "
                    f"(conf {conf:.3f})", ext="art"
                )
                return _validate_art_result(clip_result)
            elif conf >= clip_accept and unknown_artist:
                # High confidence but no named artist — keep as fallback candidate,
                # still try cloud for a proper identification
                logger.info(
                    f"[art-enrich] CLIP confident ({conf:.3f}) but artist unknown — "
                    f"trying cloud.", ext="art"
                )
            elif conf < clip_fallback:
                # Too low — discard CLIP result, fall through to cloud
                logger.info(
                    f"[art-enrich] CLIP below fallback threshold "
                    f"({conf:.3f} < {clip_fallback}) — trying cloud.", ext="art"
                )
                clip_result = None
            # else: between thresholds — keep as candidate but still try cloud

    # ── Tier 2: Cloud ──────────────────────────────────────────────────────
    google_key     = settings.get('google:vision_api_key') or ''
    bing_key       = settings.get('bing:visual_search_api_key') or ''
    primary        = (settings.get('art:cloud_provider') or 'google').lower()
    cloud_fallback = float(settings.get('art:cloud_fallback_threshold') or 0.70)
    google_limit   = int(settings.get('art:google_monthly_limit') or 1000)
    bing_limit     = int(settings.get('art:bing_monthly_limit') or 1000)

    def _google_available():
        return (google_key and
                google_limit > 0 and
                _get_cloud_call_count('google') < google_limit)

    def _bing_available():
        return (bing_key and
                bing_limit > 0 and
                _get_cloud_call_count('bing') < bing_limit)

    providers = (
        ['google', 'bing'] if primary == 'google'
        else ['bing', 'google']
    )

    best_cloud = None
    for provider in providers:
        if provider == 'google':
            if not _google_available():
                continue
            try:
                result = _call_google_vision(image_path, google_key)
                _increment_cloud_call('google')
            except requests.HTTPError as e:
                raise  # caller handles 429
            except Exception as e:
                logger.warn(f"[art-enrich] Google Vision error: {e}", ext="art")
                continue
        else:  # bing
            if not _bing_available():
                continue
            try:
                result = _call_bing_visual_search(image_path, bing_key)
                _increment_cloud_call('bing')
            except requests.HTTPError as e:
                raise  # caller handles 429
            except Exception as e:
                logger.warn(f"[art-enrich] Bing Visual Search error: {e}", ext="art")
                continue

        if best_cloud is None or result['confidence'] > best_cloud['confidence']:
            best_cloud = result

        # If primary is good enough, skip secondary
        if result['confidence'] >= cloud_fallback:
            break

    if best_cloud and best_cloud.get('title'):
        # Prefer cloud over a weak CLIP candidate
        if clip_result is None or best_cloud['confidence'] >= clip_result['confidence']:
            return _validate_art_result(best_cloud)

    # Fall back to CLIP candidate (between thresholds) if cloud had nothing better
    if clip_result and clip_result.get('title'):
        return _validate_art_result(clip_result)

    # Nothing found
    return None


# ── Main worker loop ──────────────────────────────────────────────────────────

def run(db_path: str, shutdown_event=None):
    """
    Main loop for the art enrichment worker.
    db_path: path to docvault.db (for task path updates after rename).
    """
    logger.info("Art Enrichment worker starting.", ext="art")

    from core.settings import settings
    from core.vault_manager import VaultManager
    from core.manager import get_settings_db_path

    settings_db  = get_settings_db_path()
    backoff_until: datetime | None = None   # pause API calls until this time

    while True:
        if shutdown_event and shutdown_event.is_set():
            break

        # ── Read settings fresh each cycle ────────────────────────────────
        rpm       = int(settings.get('art:enrichment_requests_per_minute') or 5)
        threshold = float(settings.get('art:enrichment_confidence_threshold') or 0.85)
        retry_hrs = int(settings.get('art:enrichment_retry_hours') or 24)

        # Quick check: is any identification method available?
        clip_on    = str(settings.get('art:clip_enabled') or 'true').lower() == 'true'
        google_key = settings.get('google:vision_api_key') or ''
        bing_key   = settings.get('bing:visual_search_api_key') or ''

        if not clip_on and not google_key and not bing_key:
            # Nothing configured — wait for user to set up at least one method
            time.sleep(300)
            continue

        # ── Only run when IDLE ─────────────────────────────────────────────
        skip, state = should_pause_or_throttle()
        if skip or state != 'normal':
            time.sleep(30)
            continue

        # ── Respect API backoff (for 429s) ────────────────────────────────
        if backoff_until and datetime.now(timezone.utc) < backoff_until:
            time.sleep(60)
            continue
        backoff_until = None

        # ── Build work queue ───────────────────────────────────────────────
        try:
            vm         = VaultManager(db_path)
            vaults     = vm.list_vaults()
            vault_dirs = [
                v['scan_directory'] for v in vaults
                if v['state'] in ('active', 'archived')
            ]
        except Exception as e:
            logger.error(f"Art enrichment: could not load vaults: {e}", ext="art")
            time.sleep(60)
            continue

        queue  = _find_unprocessed(vault_dirs)
        queue += _find_retry_eligible(vault_dirs, retry_hrs)

        if not queue:
            time.sleep(600)
            continue

        logger.info(f"Art enrichment: {len(queue)} image(s) to process.", ext="art")
        bucket = _TokenBucket(rpm)

        for image_path in queue:
            try:
                if shutdown_event and shutdown_event.is_set():
                    break

                # Re-check throttle before each image
                skip, state = should_pause_or_throttle()
                if skip or state != 'normal':
                    logger.info("Art enrichment pausing — system no longer idle.", ext="art")
                    break

                # Re-check backoff (may have been set during this batch)
                if backoff_until and datetime.now(timezone.utc) < backoff_until:
                    break

                # Skip if .nfo appeared since we built the queue (already processed)
                if os.path.exists(_nfo_path(image_path)):
                    data = _read_nfo(image_path)
                    if data.get('renamed_to') != '(failed)':
                        continue

                logger.info(
                    f"Art enrichment: identifying {os.path.basename(image_path)}", ext="art"
                )

                # Rate limit
                bucket.consume()

                today = datetime.now().isoformat()[:10]

                try:
                    result = _identify_artwork(image_path)
                except requests.HTTPError as e:
                    status_code = e.response.status_code if e.response is not None else 0
                    if status_code == 429:
                        current_backoff = getattr(run, '_backoff_secs', 60)
                        run._backoff_secs = min(current_backoff * 2, 3600)
                        backoff_until = datetime.now(timezone.utc) + timedelta(
                            seconds=run._backoff_secs
                        )
                        logger.warn(
                            f"Art enrichment: 429 rate limit — backing off "
                            f"{run._backoff_secs}s.", ext="art"
                        )
                        _write_nfo(image_path, {
                            'artist': '', 'title': '', 'confidence': '0',
                            'identified_at': today,
                            'original_name': os.path.basename(image_path),
                            'renamed_to': '(failed)',
                            'error': f'HTTP {status_code} — rate limited',
                        })
                        break
                    else:
                        logger.error(
                            f"Art enrichment: API error {status_code} for "
                            f"{os.path.basename(image_path)}", ext="art"
                        )
                        _write_nfo(image_path, {
                            'artist': '', 'title': '', 'confidence': '0',
                            'identified_at': today,
                            'original_name': os.path.basename(image_path),
                            'renamed_to': '(failed)',
                            'error': f'HTTP {status_code}',
                        })
                        continue
                except Exception as e:
                    logger.error(
                        f"Art enrichment: unexpected error for "
                        f"{os.path.basename(image_path)}: {e}", ext="art"
                    )
                    _write_nfo(image_path, {
                        'artist': '', 'title': '', 'confidence': '0',
                        'identified_at': today,
                        'original_name': os.path.basename(image_path),
                        'renamed_to': '(failed)',
                        'error': str(e),
                    })
                    continue

                if result is None:
                    # No method produced a result (CLIP absent, no cloud keys, etc.)
                    _write_nfo(image_path, {
                        'artist': '', 'title': '', 'confidence': '0',
                        'identified_at': today,
                        'original_name': os.path.basename(image_path),
                        'renamed_to': '(failed)',
                        'error': 'no identification method available',
                    })
                    continue

                # Reset backoff on success
                run._backoff_secs = 60

                artist     = result.get('artist', '')
                title      = result.get('title', '')
                confidence = result.get('confidence', 0.0)
                source_url = result.get('source_url', '')
                tier_used  = result.get('tier', 'unknown')

                nfo_data = {
                    'artist':        artist or '',
                    'title':         title or '',
                    'confidence':    str(round(confidence, 4)),
                    'source':        tier_used,
                    'source_url':    source_url or '',
                    'identified_at': today,
                    'original_name': os.path.basename(image_path),
                    'renamed_to':    '(low confidence — manual review)',
                }

                if confidence >= threshold and (artist or title):
                    ok, result_path = _transactional_rename(
                        image_path, artist, title, db_path
                    )
                    if ok:
                        nfo_data['renamed_to'] = os.path.basename(result_path)
                        logger.info(
                            f"Art enrichment: renamed → {os.path.basename(result_path)} "
                            f"[{tier_used}] (confidence {confidence:.2f})", ext="art"
                        )
                        _write_nfo(result_path, nfo_data)
                    else:
                        nfo_data['renamed_to'] = '(failed)'
                        nfo_data['error']      = result_path
                        logger.warn(
                            f"Art enrichment: rename failed for "
                            f"{os.path.basename(image_path)}: {result_path}", ext="art"
                        )
                        # _transactional_rename already wrote the .nfo
                else:
                    _write_nfo(image_path, nfo_data)
                    logger.info(
                        f"Art enrichment: [{tier_used}] low confidence ({confidence:.2f}) for "
                        f"{os.path.basename(image_path)} — .nfo written, file unchanged.", ext="art"
                    )
            except Exception as _loop_err:
                logger.error(
                    f"Art enrichment: unhandled error for "
                    f"{os.path.basename(image_path)} — skipping: {_loop_err}",
                    ext="art"
                )
                continue

        # End of queue — sleep before next scan cycle
        time.sleep(300)
