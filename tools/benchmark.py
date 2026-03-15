"""
tools/benchmark.py — DocVault standalone benchmark tool.

Samples COMPLETED tasks from docvault.db, runs their extractors
in-process (timing only, no DB writes), prints a report.

Usage:
    python tools/benchmark.py [--samples N] [--types TYPE,...] [--vault ID|all] [--no-save]
"""
import argparse
import json
import os
import random
import sys
import sqlite3
import threading
import time
from datetime import datetime, timezone

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core import manager
from core import router as _router
from core.extractors.base import LegacyExtractorAdapter, ExtractorContext, ExtractorLogger, BaseExtractor
from core.settings import SettingsResolver, settings


TYPE_EXTENSIONS = {
    'text':  {'txt', 'md', 'py', 'js', 'ts', 'json', 'xml', 'csv', 'html', 'css', 'yml', 'yaml', 'toml'},
    'pdf':   {'pdf'},
    'image': {'jpg', 'jpeg', 'png', 'bmp', 'gif', 'tiff', 'webp'},
    'audio': {'mp3', 'opus', 'wav', 'flac', 'm4a', 'ogg'},
    'video': {'mp4', 'avi', 'mkv', 'mov', 'wmv'},
}


def _classify(file_type: str) -> str | None:
    ext = (file_type or '').lstrip('.').lower()
    for cat, exts in TYPE_EXTENSIONS.items():
        if ext in exts:
            return cat
    return None


def _sample_tasks(db_path: str, n: int, types: list[str], vault_id: str | None) -> dict[str, list[dict]]:
    """Return {category: [task_row, ...]} from COMPLETED tasks."""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        sql = "SELECT file_hash, file_path, file_type, file_size, vault_id FROM tasks WHERE status='COMPLETED'"
        params = []
        if vault_id:
            sql += " AND vault_id=?"
            params.append(vault_id)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    buckets: dict[str, list] = {t: [] for t in types}
    for row in rows:
        cat = _classify(row['file_type'])
        if cat and cat in buckets:
            buckets[cat].append(dict(row))

    return {cat: random.sample(items, min(n, len(items))) for cat, items in buckets.items()}


def _time_file(task: dict) -> tuple[str | None, float]:
    """
    Run extractors for one file. Returns (bottleneck_extractor, elapsed_secs).
    Does NOT write any results to docvault.db.
    """
    file_path = task['file_path']
    file_type = (task['file_type'] or '').lstrip('.')
    vault_id  = task.get('vault_id') or ''

    extractors = _router.get_extractors(file_type, vault_id=vault_id)
    if not extractors:
        return None, 0.0

    cancel_token = threading.Event()
    ext_logger   = ExtractorLogger('benchmark', vault_id, task['file_hash'])
    ctx = ExtractorContext(
        vault_id=vault_id,
        file_hash=task['file_hash'],
        cancel_token=cancel_token,
        logger=ext_logger,
        settings=SettingsResolver(vault_id=vault_id),
    )

    slowest_name, slowest_elapsed, total = None, 0.0, 0.0
    for ext_mod in extractors:
        adapter = ext_mod if isinstance(ext_mod, BaseExtractor) else LegacyExtractorAdapter(ext_mod)
        t0 = time.monotonic()
        try:
            adapter.run(file_path, ctx)   # result discarded — timing only
        except Exception:
            pass
        elapsed = time.monotonic() - t0
        total  += elapsed
        if elapsed > slowest_elapsed:
            slowest_elapsed = elapsed
            slowest_name = adapter.name

    return slowest_name, total


def run_benchmark(db_path: str, n: int, types: list[str], vault_id: str | None) -> dict:
    """Core benchmark logic. Returns structured results dict."""
    samples = _sample_tasks(db_path, n, types, vault_id)

    results = {}
    overall_bottleneck = None
    overall_bottleneck_avg = 0.0

    for cat, tasks in samples.items():
        if not tasks:
            results[cat] = {'samples': 0, 'avg_secs': 0.0, 'p95_secs': 0.0,
                            'throughput': 0.0, 'bottleneck': None}
            continue

        timings = []
        cat_bottleneck, cat_bottleneck_time = None, 0.0
        for task in tasks:
            if not os.path.exists(task['file_path']):
                continue
            slow_ext, elapsed = _time_file(task)
            timings.append(elapsed)
            if elapsed > cat_bottleneck_time:
                cat_bottleneck_time = elapsed
                cat_bottleneck = slow_ext

        if not timings:
            results[cat] = {'samples': 0, 'avg_secs': 0.0, 'p95_secs': 0.0,
                            'throughput': 0.0, 'bottleneck': None}
            continue

        timings.sort()
        avg = sum(timings) / len(timings)
        p95 = timings[max(0, int(len(timings) * 0.95) - 1)] if timings else 0.0
        throughput = 3600.0 / avg if avg > 0 else 0.0

        results[cat] = {
            'samples': len(timings),
            'avg_secs': round(avg, 2),
            'p95_secs': round(p95, 2),
            'throughput': round(throughput, 0),
            'bottleneck': cat_bottleneck,
        }
        if avg > overall_bottleneck_avg:
            overall_bottleneck_avg = avg
            overall_bottleneck = cat_bottleneck

    return {'by_type': results, 'bottleneck_extractor': overall_bottleneck}


def _get_pending_counts(db_path: str, types: list[str]) -> dict[str, int]:
    """Query PENDING task counts from docvault.db, classified by category."""
    counts: dict[str, int] = {t: 0 for t in types}
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            rows = conn.execute(
                "SELECT file_type, COUNT(*) FROM tasks WHERE status='PENDING' GROUP BY file_type"
            ).fetchall()
        finally:
            conn.close()
        for file_type, count in rows:
            cat = _classify(file_type)
            if cat and cat in counts:
                counts[cat] += count
    except Exception:
        pass
    return counts


def _print_report(results: dict, db_path: str):
    print(f"\nDocVault Benchmark — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    cs   = settings.get('embeddings:chunk_size')
    mp   = settings.get('ollama:max_parallel')
    emb  = settings.get('ollama:embed_model')
    print(f"Settings: chunk_size={cs}  max_parallel={mp}  embed_model={emb}")
    print("Note: throughput is in-process (excludes queue/DB overhead)\n")

    hdr = f"{'File type':<12}  {'Samples':>7}  {'Avg time':>9}  {'P95':>7}  {'Throughput (in-process)':>23}"
    print(hdr)
    print('-' * len(hdr))

    # Identify bottleneck category in a pre-pass so the marker is correct
    bottleneck_cat = max(
        (cat for cat, r in results['by_type'].items() if r['samples'] > 0),
        key=lambda cat: results['by_type'][cat]['avg_secs'],
        default=None,
    )
    for cat, r in results['by_type'].items():
        if r['samples'] == 0:
            print(f"{cat:<12}  {'—':>7}  {'—':>9}  {'—':>7}  {'no COMPLETED files':>23}")
            continue
        marker = '  ← BOTTLENECK' if cat == bottleneck_cat else ''
        print(f"{cat:<12}  {r['samples']:>7}  {r['avg_secs']:>8.2f}s  {r['p95_secs']:>6.2f}s  {r['throughput']:>20,.0f} f/hr{marker}")

    # Overall weighted throughput
    types = list(results['by_type'].keys())
    pending = _get_pending_counts(db_path, types)
    by_type = results['by_type']

    weighted_sum = sum(
        by_type[cat]['throughput'] * pending[cat]
        for cat in types
        if by_type[cat]['throughput'] > 0 and pending.get(cat, 0) > 0
    )
    weight_total = sum(
        pending[cat]
        for cat in types
        if by_type[cat]['throughput'] > 0 and pending.get(cat, 0) > 0
    )

    if weight_total > 0:
        overall = weighted_sum / weight_total
    else:
        non_zero = [by_type[cat]['throughput'] for cat in types if by_type[cat]['throughput'] > 0]
        overall = sum(non_zero) / len(non_zero) if non_zero else 0.0

    print(f"\nOverall (weighted by queue depth): {overall:,.0f} f/hr (in-process)")

    # Queue drain estimate
    total_drain_hours = 0.0
    has_drain = False
    for cat in types:
        tp = by_type[cat]['throughput']
        pend = pending.get(cat, 0)
        if tp > 0 and pend > 0:
            total_drain_hours += pend / tp
            has_drain = True
    if has_drain:
        print(f"Queue drain estimate: ~{total_drain_hours:.1f} hours at current speed")

    # Bottleneck line
    if bottleneck_cat:
        bn = by_type[bottleneck_cat]
        print(f"\nBottleneck: {bn['bottleneck'] or bottleneck_cat} ({bn['avg_secs']}s avg)")

    # Tip based on bottleneck category
    tips = {
        'image': "Tip: Disabling vision for non-art vaults would increase throughput significantly.",
        'audio': "Tip: Disabling Whisper transcription would reduce audio processing time.",
        'video': "Tip: Limiting video frame extraction would improve video throughput.",
    }
    tip = tips.get(bottleneck_cat, "Tip: Consider increasing max_parallel if GPU headroom is available.")
    print(tip)

    print()


def save_result(results: dict, db_path: str):
    """Write one row to logs.db benchmark_runs."""
    from core.manager import get_logs_db_path, _connect, init_logs_db
    # Ensure logs.db schema exists (idempotent — safe even when server hasn't run yet)
    init_logs_db()
    bottleneck = results.get('bottleneck_extractor')
    by_type    = results['by_type']

    # Weighted overall throughput (matches what _print_report() prints)
    types = list(by_type.keys())
    pending = _get_pending_counts(db_path, types)
    weighted_sum = sum(
        by_type[cat]['throughput'] * pending[cat]
        for cat in types
        if by_type[cat]['throughput'] > 0 and pending.get(cat, 0) > 0
    )
    weight_total = sum(
        pending[cat]
        for cat in types
        if by_type[cat]['throughput'] > 0 and pending.get(cat, 0) > 0
    )
    if weight_total > 0:
        overall = weighted_sum / weight_total
    else:
        non_zero = [by_type[cat]['throughput'] for cat in types if by_type[cat]['throughput'] > 0]
        overall = sum(non_zero) / len(non_zero) if non_zero else 0.0

    with _connect(get_logs_db_path()) as conn:
        conn.execute(
            """INSERT INTO benchmark_runs (run_at, results, bottleneck_extractor, overall_files_per_hour)
               VALUES (?, ?, ?, ?)""",
            (datetime.now(timezone.utc).isoformat(),
             json.dumps(by_type),
             bottleneck,
             round(overall, 1))
        )
        conn.commit()
        run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    print(f"Results saved to logs.db (run_id: {run_id})")


def main():
    ap = argparse.ArgumentParser(description='DocVault benchmark tool')
    ap.add_argument('--samples', type=int, default=None,
                    help='Files per type (default: tuning:benchmark_samples_per_type setting)')
    ap.add_argument('--types', default='text,pdf,image,audio,video',
                    help='Comma-separated type categories')
    ap.add_argument('--vault', default='all',
                    help='Vault ID or "all"')
    ap.add_argument('--no-save', action='store_true',
                    help='Skip saving to logs.db')
    args = ap.parse_args()

    n = args.samples or int(settings.get('tuning:benchmark_samples_per_type') or 10)
    types = [t.strip() for t in args.types.split(',') if t.strip() in TYPE_EXTENSIONS]
    vault_id = None if args.vault == 'all' else args.vault
    db_path  = manager.get_db_path()

    results = run_benchmark(db_path, n, types, vault_id)
    _print_report(results, db_path)
    if not args.no_save:
        save_result(results, db_path)


if __name__ == '__main__':
    main()
