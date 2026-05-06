import os
import subprocess
from fastapi import APIRouter
from pydantic import BaseModel
from core import manager
from core.settings import settings
from core.vault_manager import VaultManager

# Extensions that must never be opened via the API regardless of vault membership.
_BLOCKED_EXTENSIONS = {
    '.exe', '.com', '.msi', '.bat', '.cmd', '.ps1', '.vbs', '.wsf',
    '.scr', '.pif', '.dll', '.sys', '.reg', '.hta', '.lnk', '.url',
    '.cpl', '.inf', '.js', '.jse', '.vbe',
}


def _is_in_vault_root(path: str) -> bool:
    """Return True iff *path* resolves to a location inside an active or
    archived vault's scan_directory.  Uses realpath to block symlink escapes."""
    try:
        real = os.path.realpath(path)
        vm = VaultManager(manager.get_db_path())
        for vault in vm.list_vaults():
            if vault['state'] not in ('active', 'archived'):
                continue
            vault_root = os.path.realpath(vault['scan_directory'])
            if real.startswith(vault_root + os.sep) or real == vault_root:
                return True
        return False
    except Exception:
        return False
router = APIRouter()


# ── Performance Stats ─────────────────────────────────────────────────────────

@router.get("/utils/performance_stats")
def performance_stats():
    """
    Returns extractor timing stats, throughput trends, bottleneck callout,
    and last 5 benchmark runs for the Analytics dashboard.
    """
    from core.manager import get_logs_db_path, _connect
    import sqlite3

    logs_db = get_logs_db_path()
    import math
    import collections

    try:
        with _connect(logs_db) as conn:
            # Extractor stats
            rows = conn.execute(
                """SELECT extractor,
                          AVG(elapsed_secs) AS avg_secs,
                          COUNT(*) AS cnt
                   FROM task_timings
                   WHERE elapsed_secs IS NOT NULL
                   GROUP BY extractor
                   ORDER BY avg_secs DESC"""
            ).fetchall()

            # Pre-fetch all elapsed values by extractor (avoids N+1 queries)
            all_elapsed_rows = conn.execute(
                "SELECT extractor, elapsed_secs FROM task_timings WHERE elapsed_secs IS NOT NULL ORDER BY extractor, elapsed_secs"
            ).fetchall()
            elapsed_by_extractor = collections.defaultdict(list)
            for row in all_elapsed_rows:
                elapsed_by_extractor[row['extractor']].append(row['elapsed_secs'])

            extractor_stats = []
            for r in rows:
                avg = r['avg_secs']
                category = 'fast' if avg < 1 else ('moderate' if avg < 10 else 'slow')
                elapseds = elapsed_by_extractor.get(r['extractor'], [])
                p95_idx = min(len(elapseds) - 1, max(0, math.ceil(len(elapseds) * 0.95) - 1))
                p95 = elapseds[p95_idx] if elapseds else 0.0
                extractor_stats.append({
                    'extractor': r['extractor'], 'avg_secs': round(avg, 2),
                    'p95_secs': round(p95, 2), 'count': r['cnt'], 'category': category,
                })

            # Throughput 24h (hourly bins)
            rows_24h = conn.execute(
                """SELECT strftime('%H:00', completed_at) AS hour, COUNT(*) AS cnt
                   FROM task_timings
                   WHERE completed_at >= datetime('now', '-24 hours')
                   GROUP BY hour ORDER BY hour"""
            ).fetchall()
            throughput_24h = [{'hour': r['hour'], 'files_per_hour': r['cnt']} for r in rows_24h]

            # Throughput 7d (daily bins)
            rows_7d = conn.execute(
                """SELECT date(completed_at) AS day, COUNT(*) AS cnt
                   FROM task_timings
                   WHERE completed_at >= datetime('now', '-7 days')
                   GROUP BY day ORDER BY day"""
            ).fetchall()
            throughput_7d = [{'day': r['day'], 'files_per_hour': r['cnt']} for r in rows_7d]

            # Bottleneck (slowest extractor)
            bottleneck = None
            if extractor_stats:
                b = extractor_stats[0]
                tip = (f"Your slowest extractor is {b['extractor']} ({b['avg_secs']}s avg, "
                       f"{b['count']} files processed). Consider disabling it for vaults that don't need it.")
                bottleneck = {'extractor': b['extractor'], 'avg_secs': b['avg_secs'], 'tip': tip}

            # Last 5 benchmark runs
            bench_rows = conn.execute(
                """SELECT run_id, run_at, overall_files_per_hour, bottleneck_extractor
                   FROM benchmark_runs ORDER BY run_id DESC LIMIT 5"""
            ).fetchall()
            benchmark_runs = [dict(r) for r in bench_rows]

    except Exception:
        return {
            'extractor_stats': [],
            'throughput_24h':  [],
            'throughput_7d':   [],
            'bottleneck':      None,
            'benchmark_runs':  [],
        }

    return {
        'extractor_stats': extractor_stats,
        'throughput_24h':  throughput_24h,
        'throughput_7d':   throughput_7d,
        'bottleneck':      bottleneck,
        'benchmark_runs':  benchmark_runs,
    }


# ── Optimizer Endpoints ───────────────────────────────────────────────────────

class OptimizerApplyRequest(BaseModel):
    profile: str  # "raw_speed" | "sustainable" | "balanced"


class OptimizerStartRequest(BaseModel):
    samples:       int | None       = None
    types:         list[str] | None = None
    locked_axes:   list[str] | None = None
    force_parallel: bool            = False
    vault_id:      str | None       = None
    delay_hours:   float            = 0


class SaveProfileRequest(BaseModel):
    name:       str
    params:     dict
    throughput: float | None = None
    source_run: dict | None  = None


@router.post("/utils/optimizer/start")
def optimizer_start(req: OptimizerStartRequest | None = None):
    """Launch the optimizer. 409 if already running. Accepts optional config body."""
    from api.main import DB_PATH
    from core.tuner import start_optimizer
    config = req.model_dump() if req else {}
    if not start_optimizer(DB_PATH, config=config):
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="optimizer already running")
    state = config.get('delay_hours', 0) and config['delay_hours'] > 0
    return {"state": "scheduled" if state else "starting"}


@router.get("/utils/optimizer/last_config")
def optimizer_last_config():
    """Return the config dict from the last sweep, or {} if none."""
    from core.tuner import get_last_config
    cfg = get_last_config()
    return cfg if cfg is not None else {}


@router.post("/utils/optimizer/save_profile")
def optimizer_save_profile(req: SaveProfileRequest):
    """Save an optimizer profile to logs.db optimizer_profiles table."""
    import json
    from datetime import datetime, timezone
    from core.manager import get_logs_db_path, _connect, init_logs_db
    init_logs_db()
    saved_at = datetime.now(timezone.utc).isoformat()
    with _connect(get_logs_db_path()) as conn:
        cur = conn.execute(
            """INSERT INTO optimizer_profiles (saved_at, name, params, throughput, source_run)
               VALUES (?, ?, ?, ?, ?)""",
            (
                saved_at,
                req.name,
                json.dumps(req.params),
                req.throughput,
                json.dumps(req.source_run) if req.source_run else None,
            )
        )
        conn.commit()
        profile_id = cur.lastrowid
    return {"saved": True, "profile_id": profile_id}


@router.get("/utils/optimizer/saved_profiles")
def optimizer_saved_profiles():
    """Return last 10 saved optimizer profiles ordered by saved_at DESC."""
    import json
    from core.manager import get_logs_db_path, _connect, init_logs_db
    init_logs_db()
    with _connect(get_logs_db_path()) as conn:
        rows = conn.execute(
            """SELECT profile_id, saved_at, name, params, throughput, source_run
               FROM optimizer_profiles ORDER BY saved_at DESC LIMIT 10"""
        ).fetchall()
    result = []
    for r in rows:
        row = dict(r)
        try:
            row['params'] = json.loads(row['params'])
        except Exception:
            pass
        try:
            if row['source_run']:
                row['source_run'] = json.loads(row['source_run'])
        except Exception:
            pass
        result.append(row)
    return result


@router.get("/utils/optimizer/status")
def optimizer_status():
    """Live poll — frontend calls every 1000ms."""
    from core.tuner import get_state
    return get_state()


@router.post("/utils/optimizer/apply")
def optimizer_apply(req: OptimizerApplyRequest):
    """Apply a completed optimizer profile to settings.db."""
    from core.tuner import get_state
    from fastapi import HTTPException
    state = get_state()
    if state['state'] != 'complete':
        raise HTTPException(status_code=400, detail="no complete run to apply")
    profiles = state.get('profiles') or {}
    profile = profiles.get(req.profile)
    if not profile:
        raise HTTPException(status_code=400, detail=f"unknown profile: {req.profile}")
    from core.settings import settings
    params = profile['params']
    failed = {}
    for k, v in params.items():
        try:
            settings.set(k, v)
        except Exception as exc:
            failed[k] = str(exc)
    result = {"applied": True, "params": params}
    if failed:
        result["warnings"] = failed
    return result


@router.post("/utils/optimizer/abort")
def optimizer_abort():
    """Abort the running optimizer. 400 if not running."""
    from core.tuner import abort_optimizer, get_state
    from fastapi import HTTPException
    s = get_state()['state']
    if s in ('idle', 'complete', 'aborted', 'error'):
        raise HTTPException(status_code=400, detail="no active run")
    abort_optimizer()
    return {"state": "aborting"}


@router.get("/utils/qdrant_check")
def run_qdrant_check():
    """Health check for the vector store."""
    try:
        from embeddings.vector_store import VectorStore
        count = VectorStore().count()
        return {'status': 'ok', 'vectors': count}
    except Exception as e:
        return {'status': 'error', 'detail': str(e)}


class OpenRequest(BaseModel):
    path: str
    action: str  # 'file' or 'folder'


@router.post("/utils/reindex")
def reindex_all():
    """
    Wipe the vector collection and reset all COMPLETED tasks to EXTRACTED
    so the embedding worker re-embeds everything from scratch.
    Extracted text, FTS index, settings, and file metadata are untouched.
    """
    from api.main import DB_PATH
    from embeddings.vector_store import VectorStore

    VectorStore().drop_and_recreate()

    import sqlite3
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        cur = conn.execute(
            "UPDATE tasks SET status='EXTRACTED', worker_id=NULL WHERE status='COMPLETED'"
        )
        reset_count = cur.rowcount

    return {'status': 'ok', 'reset_tasks': reset_count}


@router.get("/utils/gdrive_status")
def gdrive_status():
    """Check whether Google Drive OAuth token exists."""
    from extractors.gdrive_extractor import is_authorized
    return {'authorized': is_authorized()}


@router.post("/utils/gdrive_authorize")
def gdrive_authorize():
    """
    Run the Google Drive OAuth flow (opens a browser window).
    Returns {ok: true} on success or {ok: false, detail: '...'} on failure.
    """
    from extractors.gdrive_extractor import ensure_authorized
    ok, err = ensure_authorized()
    if ok:
        return {'ok': True}
    return {'ok': False, 'detail': err}


@router.post("/utils/clear_logs")
def clear_logs():
    from core.manager import get_logs_db_path, _connect
    with _connect(get_logs_db_path()) as conn:
        conn.executescript("""
            DELETE FROM task_timings;
            DELETE FROM worker_errors;
            DELETE FROM worker_log;
            DELETE FROM extractor_stats;
            DELETE FROM system_stats;
        """)
        conn.commit()
    return {"ok": True}


@router.get("/utils/health")
def system_health():
    """
    Diagnose common task-queue problems.
    Returns a list of issues with severity and available fix actions.
    """
    from api.main import DB_PATH
    import sqlite3

    issues = []

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row

    try:
        # Count by status
        rows = conn.execute(
            "SELECT status, COUNT(*) as n FROM tasks GROUP BY status"
        ).fetchall()
        counts = {r['status']: r['n'] for r in rows}

        # 1. Stuck active tasks (worker process died, task will never complete)
        threshold_mins = int(manager.get_setting('monitor:stuck_task_threshold_mins') or 10)
        stuck_rows = conn.execute(
            """SELECT COUNT(*) as n FROM tasks 
               WHERE status IN ('PROCESSING', 'EMBEDDING')
               AND last_update <= datetime('now', ?)""",
            (f'-{threshold_mins} minutes',)
        ).fetchone()
        stuck = stuck_rows['n']
        
        if stuck:
            issues.append({
                'id': 'stuck_processing',
                'severity': 'warning',
                'title': f'{stuck} stuck task{"s" if stuck != 1 else ""}',
                'detail': f'Inactive for >{threshold_mins}m. Claimed by processes that likely no longer exist.',
                'action': 'reset_stuck',
                'action_label': f'Reset {stuck} to PENDING',
                'count': stuck,
            })

        # 2. Embedding failures (safe to retry — transient vector store error)
        qdrant_errs = conn.execute(
            """SELECT COUNT(*) as n FROM tasks
               WHERE status='ERROR'
               AND (error_log LIKE '%upsert%' OR error_log LIKE '%Qdrant%'
                    OR error_log LIKE '%timed out%')"""
        ).fetchone()['n']
        if qdrant_errs:
            issues.append({
                'id': 'qdrant_errors',
                'severity': 'warning',
                'title': f'{qdrant_errs} embedding failure{"s" if qdrant_errs != 1 else ""}',
                'detail': 'Extracted text is intact. Failed only at the embedding/upload step. Safe to retry.',
                'action': 'retry_embed_errors',
                'action_label': f'Retry {qdrant_errs} (reset to EXTRACTED)',
                'count': qdrant_errs,
            })

        # 3. Encoding errors (need a code fix — don't auto-retry)
        enc_errs = conn.execute(
            """SELECT COUNT(*) as n FROM tasks
               WHERE status='ERROR'
               AND (error_log LIKE '%charmap%' OR error_log LIKE '%codec%encode%')"""
        ).fetchone()['n']
        if enc_errs:
            issues.append({
                'id': 'encoding_errors',
                'severity': 'info',
                'title': f'{enc_errs} Unicode encoding error{"s" if enc_errs != 1 else ""}',
                'detail': 'OCR extractor failed encoding Unicode characters. Requires a code fix before retrying.',
                'action': 'retry_extract_errors',
                'action_label': f'Retry {enc_errs} anyway (reset to PENDING)',
                'count': enc_errs,
            })

        # 4. Other errors (empty files, genuine failures — info only)
        other_errs = counts.get('ERROR', 0) - qdrant_errs - enc_errs
        if other_errs > 0:
            issues.append({
                'id': 'other_errors',
                'severity': 'info',
                'title': f'{other_errs} other extraction error{"s" if other_errs != 1 else ""}',
                'detail': 'Empty files, unsupported formats, or missing external services. Inspect the Vault Log for details.',
                'action': None,
                'action_label': None,
                'count': other_errs,
            })
    finally:
        conn.close()

    # 5. Vector store connectivity
    vs_ok = False
    vs_points = 0
    try:
        from embeddings.vector_store import VectorStore
        vs_points = VectorStore().count()
        vs_ok = True
    except Exception as e:
        issues.append({
            'id': 'qdrant_down',
            'severity': 'error',
            'title': 'Vector store unavailable',
            'detail': f'Embedding worker cannot store vectors: {e}',
            'action': None,
            'action_label': None,
            'count': 0,
        })

    return {
        'issues': issues,
        'counts': counts,
        'qdrant': {'ok': vs_ok, 'points': vs_points},
    }


@router.get("/utils/stuck_tasks")
def list_stuck_tasks():
    """List tasks currently in PROCESSING or EMBEDDING state that exceed threshold."""
    from api.main import DB_PATH
    import sqlite3
    threshold_mins = int(manager.get_setting('monitor:stuck_task_threshold_mins') or 10)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT t.*, v.name as vault_name 
               FROM tasks t 
               LEFT JOIN vaults v ON t.vault_id = v.vault_id
               WHERE t.status IN ('PROCESSING', 'EMBEDDING')
               AND t.last_update <= datetime('now', ?)
               ORDER BY t.last_update ASC""",
            (f'-{threshold_mins} minutes',)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/utils/retry_task")
def retry_task(file_hash: str):
    """Reset a specific task to PENDING."""
    from api.main import DB_PATH
    n = _db_write(DB_PATH,
        "UPDATE tasks SET status='PENDING', worker_id=NULL, last_update=datetime('now') WHERE file_hash=?",
        (file_hash,)
    )
    return {'ok': True, 'reset': n}


@router.get("/utils/extractors")
def list_all_extractors():
    """Returns a list of all registered extractors and their settings."""
    import core.router as _router
    _router._ensure_initialized(sync_disk=False)
    from core.extractors.base import LegacyExtractorAdapter
    from core.settings import settings

    results = []
    for mod in _router._all_extractors:
        adapter = LegacyExtractorAdapter(mod)
        name = adapter.name
        
        # Identify settings related to this extractor by prefix
        prefix = name.replace('_extractor', '')
        relevant_settings = [
            s for s in settings.get_all_configurable() 
            if s['key'].startswith(prefix + ':') or s['key'].startswith(name + ':')
        ]
        
        results.append({
            'name': name,
            'settings': relevant_settings,
            'description': adapter.description
        })
    return results


class TestExtractorRequest(BaseModel):
    extractor_name: str
    file_path: str


ACTIVE_TESTS = {} # { extractor_name: threading.Event }

@router.post("/utils/test_extractor")
def test_extractor(req: TestExtractorRequest):
    """Run a specific extractor against a file and return the result."""
    from core import router
    from core.extractors.base import LegacyExtractorAdapter, ExtractorContext, ExtractorLogger, BaseExtractor
    from core.settings import SettingsResolver
    import threading
    import time
    import dataclasses
    import json as _json

    TIMEOUT_SECS = int(settings.get('lab:test_timeout_secs') or 300)

    # 1. Register cancel token
    cancel_token = threading.Event()
    ACTIVE_TESTS[req.extractor_name] = cancel_token

    ext_logger = None
    try:
        # Check if it's currently active in the router
        extractor_mod = None
        for mod in router._all_extractors:
            if getattr(mod, '__name__', '') == req.extractor_name:
                extractor_mod = mod
                break

        # 2. If not active (e.g. unverified in Lab), try to load it manually from disk
        if not extractor_mod:
            from core.registry import EXTRACTORS_DIR
            import importlib.util

            py_path = os.path.join(EXTRACTORS_DIR, f"{req.extractor_name}.py")
            json_path = os.path.join(EXTRACTORS_DIR, f"{req.extractor_name}.json")

            if os.path.exists(json_path):
                from core.extractors.base import SubprocessExtractorAdapter
                with open(json_path, 'r', encoding='utf-8') as f:
                    manifest = _json.load(f)
                extractor_mod = SubprocessExtractorAdapter(req.extractor_name, manifest.get('command', []))
            elif os.path.exists(py_path):
                spec = importlib.util.spec_from_file_location(req.extractor_name, py_path)
                extractor_mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(extractor_mod)
                extractor_mod.__name__ = req.extractor_name

        if not extractor_mod:
            return {"status": "error", "detail": f"Extractor {req.extractor_name} not found."}

        if not os.path.exists(req.file_path):
            return {"status": "error", "detail": f"File not found: {req.file_path}"}

        # Setup context
        dummy_hash = "TEST_RUN_" + str(int(time.time()))
        ext_logger = ExtractorLogger(req.extractor_name, "TEST_VAULT", dummy_hash, debug_enabled=True)
        ctx = ExtractorContext(
            vault_id="TEST_VAULT",
            file_hash=dummy_hash,
            cancel_token=cancel_token,
            logger=ext_logger,
            settings=SettingsResolver()
        )

        if isinstance(extractor_mod, BaseExtractor):
            adapter = extractor_mod
        else:
            adapter = LegacyExtractorAdapter(extractor_mod)

        # 3. Run in a thread with timeout so the request never hangs indefinitely.
        #    Heavy AI kernels may take minutes on first load; this gives them time
        #    while still returning a clean error if something truly locks up.
        result_box = {}
        error_box  = {}

        def _run():
            try:
                result_box['v'] = adapter.run(req.file_path, ctx)
            except Exception as exc:
                import traceback as _tb
                error_box['e'] = exc
                error_box['tb'] = _tb.format_exc()

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        # Poll in short intervals so a kill signal is honoured within ~0.5s
        # rather than waiting the full timeout before checking.
        deadline = time.monotonic() + TIMEOUT_SECS
        while t.is_alive() and time.monotonic() < deadline:
            t.join(timeout=0.5)
            if cancel_token.is_set():
                # Kill was requested by the user via /utils/kill_test
                return {"status": "killed", "detail": "Test was cancelled by user."}

        if t.is_alive():
            cancel_token.set()
            return {
                "status": "error",
                "detail": (
                    f"Extraction timed out after {TIMEOUT_SECS}s. "
                    "If this is an AI kernel (Whisper, vision, face), it may still be loading "
                    "its model in the background — try again in a minute."
                ),
                "traceback": ""
            }

        if 'e' in error_box:
            return {
                "status": "error",
                "detail": str(error_box['e']),
                "traceback": error_box['tb']
            }

        return {
            "status": "ok",
            "result": dataclasses.asdict(result_box['v'])
        }

    except Exception as e:
        import traceback as _tb
        tb = _tb.format_exc()
        if ext_logger:
            ext_logger.error(f"Lab Certification Crash: {e}\n{tb}")
        return {
            "status": "error",
            "detail": str(e),
            "traceback": tb
        }
    finally:
        ACTIVE_TESTS.pop(req.extractor_name, None)


@router.post("/utils/kill_test")
def kill_test(extractor_name: str):
    """Signals a running lab test to stop."""
    if extractor_name in ACTIVE_TESTS:
        ACTIVE_TESTS[extractor_name].set()
        return {"status": "ok", "message": f"Sent kill signal to {extractor_name}"}
    return {"status": "not_running", "message": "No active test found for that kernel"}


class PipelineSimulationRequest(BaseModel):
    file_path: str
    vault_id: str | None = None


@router.post("/utils/simulate_pipeline")
def simulate_pipeline(req: PipelineSimulationRequest):
    """
    Simulates the file identification and kernel routing process.
    Returns a trace of which extractors would be called and in what order.
    """
    from core import router
    from core.extractors.base import LegacyExtractorAdapter
    import os

    if not os.path.exists(req.file_path):
        return {"status": "error", "detail": f"File not found: {req.file_path}"}

    filename = os.path.basename(req.file_path)
    ext = os.path.splitext(filename)[1].lstrip('.').lower()
    
    # 1. Identification
    trace = [f"IDENTIFIED: extension '{ext}'"]
    
    # 2. Routing
    extractors = router.get_extractors(ext, vault_id=req.vault_id)
    
    if not extractors or (len(extractors) == 1 and getattr(extractors[0], '__name__', '') == 'fallback_kernel'):
        trace.append("ROUTING: No specific kernels found. Falling back to terminal SAFETY tier.")
        kernel_names = ["fallback_kernel"]
    else:
        kernel_names = [getattr(e, '__name__', str(e)) for e in extractors]
        trace.append(f"ROUTING: {len(kernel_names)} kernel(s) matched for this type.")

    return {
        "status": "ok",
        "filename": filename,
        "extension": ext,
        "trace": trace,
        "kernels": kernel_names,
        "vault_context": req.vault_id or "GLOBAL_DEFAULT"
    }


@router.get("/utils/registry")
def list_registry(sync: bool = False):
    """Returns the full contents of the ext_registry table."""
    from core.manager import get_settings_db_path, _connect
    from core.registry import RegistryManager
    
    if sync:
        RegistryManager().sync_disk_to_db()

    with _connect(get_settings_db_path()) as conn:
        rows = conn.execute("SELECT * FROM ext_registry ORDER BY last_seen_at DESC").fetchall()
        return [dict(r) for r in rows]


@router.post("/utils/certify/audit")
def certify_audit(module_name: str):
    """Performs static analysis (Manifest, Signatures) on a pending kernel."""
    from core.certification import ContractAuditor, CertificationResult
    from core.registry import EXTRACTORS_DIR
    import importlib.util
    import json
    import dataclasses
    
    py_path = os.path.join(EXTRACTORS_DIR, f"{module_name}.py")
    json_path = os.path.join(EXTRACTORS_DIR, f"{module_name}.json")
    
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                manifest = json.load(f)
            
            auditor = ContractAuditor()
            # For JSON manifests, wrap in a dummy object
            dummy_mod = type('Dummy', (), {'MANIFEST': manifest})()
            manifest_res = auditor.verify_manifest(dummy_mod)
            sig_res = CertificationResult("signatures", True, "Subprocess JSON contract verified (external binary).")
            
            return {
                "status": "ok",
                "audit": {
                    "manifest": dataclasses.asdict(manifest_res),
                    "signatures": dataclasses.asdict(sig_res)
                }
            }
        except Exception as e:
            return {"status": "error", "message": f"JSON parse error: {e}"}

    if not os.path.exists(py_path):
        return {"status": "error", "message": "Kernel file not found."}

    try:
        spec = importlib.util.spec_from_file_location(module_name, py_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        
        auditor = ContractAuditor()
        manifest_res = auditor.verify_manifest(mod)
        sig_res = auditor.verify_signatures(mod)
        
        return {
            "status": "ok",
            "audit": {
                "manifest": dataclasses.asdict(manifest_res),
                "signatures": dataclasses.asdict(sig_res)
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/utils/certify/register")
def certify_register(kernel_id: str):
    """
    Formally registers a certified kernel in the DB.
    Blesses the current disk hash and enables the kernel.
    """
    from core.registry import RegistryManager
    
    # 1. Update Database Status AND refresh the blessed hash
    rm = RegistryManager()
    rm.certify_kernel(kernel_id)

    # 2. Reload router to pick up the change (but avoid heavy disk sync)
    from core import router
    router.reload(sync_disk=False)

    return {"status": "ok", "message": f"Kernel {kernel_id} activated."}


@router.post("/utils/certify/decertify")
def decertify_register(kernel_id: str):
    """
    Removes certification from a kernel, setting it back to 'unverified'.
    """
    from core.registry import RegistryManager
    
    rm = RegistryManager()
    rm.decertify_kernel(kernel_id)

    # Reload router to pick up the change
    from core import router
    router.reload(sync_disk=False)

    return {"status": "ok", "message": f"Kernel {kernel_id} decertified."}

class BinaryRegistrationRequest(BaseModel):
    id: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    extensions: list[str]
    executable_path: str
    arguments: str  # e.g. "--file {file_path} --output-json"


@router.get("/utils/alerts")
def get_alerts(since_id: int = 0):
    """Returns the latest system alerts from the Alert Bus."""
    from core.alerts import get_session_alerts
    return get_session_alerts(since_id)


@router.post("/utils/register_binary")
def register_binary(req: BinaryRegistrationRequest):
    """
    Creates a .json manifest for an external binary extractor.
    """
    from core.registry import EXTRACTORS_DIR
    import json
    import shlex

    # Sanitize module name for filename
    module_name = req.name.lower().replace(" ", "_")
    json_path = os.path.join(EXTRACTORS_DIR, f"{module_name}.json")

    # Build the command list
    # We prepend the executable path and then parse the arguments string
    cmd_parts = [req.executable_path]
    if req.arguments:
        cmd_parts.extend(shlex.split(req.arguments))

    manifest = {
        "id": req.id,
        "version": req.version,
        "name": req.name,
        "description": req.description,
        "extensions": req.extensions,
        "type": "subprocess",
        "command": cmd_parts
    }

    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=4)
        
        # Trigger immediate sync
        from core.registry import RegistryManager
        RegistryManager().sync_disk_to_db()
        
        return {"status": "ok", "message": f"Manifest created: {module_name}.json"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _db_write(db_path, sql, params=()):
    """Execute a single write against docvault.db with WAL mode and a generous timeout."""
    import sqlite3
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


@router.post("/utils/reset_stuck")
def reset_stuck():
    """Reset all PROCESSING and EMBEDDING tasks to PENDING."""
    from api.main import DB_PATH
    n = _db_write(DB_PATH,
        "UPDATE tasks SET status='PENDING', worker_id=NULL, last_update=datetime('now') "
        "WHERE status IN ('PROCESSING', 'EMBEDDING')"
    )
    return {'ok': True, 'reset': n}


@router.post("/utils/retry_embed_errors")
def retry_embed_errors():
    """Reset embedding ERROR tasks to EXTRACTED so the embedding worker retries."""
    from api.main import DB_PATH
    n = _db_write(DB_PATH,
        """UPDATE tasks SET status='EXTRACTED', worker_id=NULL, last_update=datetime('now')
           WHERE status='ERROR'
           AND (error_log LIKE '%upsert%' OR error_log LIKE '%Qdrant%'
                OR error_log LIKE '%timed out%')"""
    )
    return {'ok': True, 'reset': n}


@router.post("/utils/retry_extract_errors")
def retry_extract_errors():
    """Reset encoding/OCR ERROR tasks to PENDING so the extraction worker retries."""
    from api.main import DB_PATH
    n = _db_write(DB_PATH,
        """UPDATE tasks SET status='PENDING', worker_id=NULL, last_update=datetime('now')
           WHERE status='ERROR'
           AND (error_log LIKE '%charmap%' OR error_log LIKE '%codec%encode%')"""
    )
    return {'ok': True, 'reset': n}


class BrowseRequest(BaseModel):
    mode: str  # 'file' or 'folder'


@router.post("/utils/browse")
def browse_path(req: BrowseRequest):
    """Opens a native Windows file/folder picker via PowerShell subprocess."""
    import subprocess
    if req.mode == 'folder':
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$d.Description = 'Select Folder'; "
            "$f = New-Object System.Windows.Forms.Form; "
            "$f.TopMost = $true; $f.ShowInTaskbar = $false; $f.WindowState = 'Minimized'; $f.Show(); "
            "$null = $d.ShowDialog($f); $f.Dispose(); "
            "Write-Output $d.SelectedPath"
        )
    else:
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d = New-Object System.Windows.Forms.OpenFileDialog; "
            "$d.Title = 'Select File'; "
            "$f = New-Object System.Windows.Forms.Form; "
            "$f.TopMost = $true; $f.ShowInTaskbar = $false; $f.WindowState = 'Minimized'; $f.Show(); "
            "$null = $d.ShowDialog($f); $f.Dispose(); "
            "Write-Output $d.FileName"
        )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=120
        )
        path = result.stdout.strip()
        return {"status": "ok", "path": path if path else None}
    except subprocess.TimeoutExpired:
        return {"status": "ok", "path": None}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@router.get("/utils/search_mode")
def get_search_mode_state():
    """Return current search mode state."""
    from core.manager import get_search_mode
    return {"enabled": get_search_mode()}


class SearchModeRequest(BaseModel):
    enabled: bool


@router.post("/utils/search_mode")
def set_search_mode_state(req: SearchModeRequest):
    """Enable or disable search mode (pauses all workers)."""
    from core.manager import set_search_mode
    set_search_mode(req.enabled)
    return {"ok": True, "enabled": req.enabled}


@router.post("/utils/shutdown")
def shutdown():
    """Signal the uvicorn server to stop gracefully (exit code 0)."""
    import api.main as _main
    if _main._server is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="server ref not available")
    _main._server.should_exit = True
    return {"ok": True}


@router.post("/utils/open_path")
def open_path(req: OpenRequest):
    # Block dangerous file extensions
    ext = os.path.splitext(req.path)[1].lower()
    if ext in _BLOCKED_EXTENSIONS:
        return {"status": "error", "detail": "File type not allowed"}
    # Restrict to vault roots (prevent arbitrary filesystem access)
    if not _is_in_vault_root(req.path):
        return {"status": "error", "detail": "Path is outside vault boundaries"}
    if not os.path.exists(req.path):
        return {"status": "error", "detail": "Path not found"}
    try:
        if req.action == 'file':
            os.startfile(req.path)
        elif req.action == 'folder':
            subprocess.Popen(['explorer', f'/select,{req.path}'])
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}
