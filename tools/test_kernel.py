#!/usr/bin/env python3
"""
DocVault Kernel Test Adapter — standalone CLI.

Run a kernel against a file without starting the server.
Output is machine-readable JSON by default so Claude can parse it directly.

USAGE
  python tools/test_kernel.py <kernel> <file> [<file2> ...]
  python tools/test_kernel.py --list
  python tools/test_kernel.py <kernel> --info
  python tools/test_kernel.py <kernel> --audit

EXAMPLES
  python tools/test_kernel.py source_code_intelligence_extractor E:/src/main.py
  python tools/test_kernel.py face_analytics_extractor E:/photos/001.jpg --pretty
  python tools/test_kernel.py plaintext_extractor E:/docs/a.txt E:/docs/b.txt
  python tools/test_kernel.py --list --pretty
  python tools/test_kernel.py aural_intelligence_extractor --info
  python tools/test_kernel.py source_code_intelligence_extractor E:/src/main.py --full-text
  python tools/test_kernel.py source_code_intelligence_extractor E:/src/main.py --output result.json

EXIT CODES
  0  pass  — extraction completed, content returned, no errors
  1  fail  — extraction ran but returned no content or reported errors
  2  timeout — did not complete within --timeout seconds
  3  crash — unhandled exception inside the extractor
  4  usage — bad arguments, file not found, kernel not found
  5  audit — contract audit failed (manifest or signature violation)
"""

import sys
import os
import json

# Force UTF-8 on Windows consoles that default to cp1252
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import time
import argparse
import threading
import dataclasses
import difflib
import traceback as _tb_mod

# ── Bootstrap ─────────────────────────────────────────────────────────────────
# Works whether invoked as `python tools/test_kernel.py` or from any cwd.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), '..'))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Silence framework noise before heavy imports land
os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL',       '3')
os.environ.setdefault('ONEDNN_VERBOSE',              '0')
os.environ.setdefault('MEDIAPIPE_DISABLE_GPU',       '1')
os.environ.setdefault('TOKENIZERS_PARALLELISM',      'false')
os.environ.setdefault('PYTHONWARNINGS',              'ignore')

# ── ANSI colours (disabled when not a TTY or on Windows without VT) ───────────
_USE_COLOR = sys.stderr.isatty() and (os.name != 'nt' or os.environ.get('TERM'))

def _c(code, text):
    return f'\033[{code}m{text}\033[0m' if _USE_COLOR else text

GRN  = lambda t: _c('92', t)
RED  = lambda t: _c('91', t)
YLW  = lambda t: _c('93', t)
BLU  = lambda t: _c('94', t)
MAG  = lambda t: _c('95', t)
DIM  = lambda t: _c('2',  t)
BOLD = lambda t: _c('1',  t)


# ── Spinner (stderr, non-blocking) ────────────────────────────────────────────
class Spinner:
    """Prints an elapsed-time ticker to stderr while a thread is running."""
    FRAMES = ['⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏'] if _USE_COLOR else ['-','\\','|','/']

    def __init__(self, label: str):
        self._label   = label
        self._stop    = threading.Event()
        self._thread  = threading.Thread(target=self._spin, daemon=True)

    def _spin(self):
        start = time.monotonic()
        i = 0
        while not self._stop.is_set():
            elapsed = time.monotonic() - start
            frame   = self.FRAMES[i % len(self.FRAMES)]
            sys.stderr.write(f'\r  {frame}  {self._label}  {elapsed:.1f}s   ')
            sys.stderr.flush()
            time.sleep(0.1)
            i += 1
        sys.stderr.write('\r' + ' ' * 60 + '\r')
        sys.stderr.flush()

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join()


# ── Kernel discovery ──────────────────────────────────────────────────────────

def _extractors_dir() -> str:
    from core.registry import EXTRACTORS_DIR
    return EXTRACTORS_DIR


def _list_kernels() -> list[dict]:
    """Return [{name, kind, manifest}] for every file in extractors/."""
    edir = _extractors_dir()
    seen = set()
    kernels = []
    for fname in sorted(os.listdir(edir)):
        if fname.startswith('_'):
            continue
        if not (fname.endswith('.py') or fname.endswith('.json')):
            continue
        name = fname.rsplit('.', 1)[0]
        kind = 'subprocess' if fname.endswith('.json') else 'python'
        if name in seen:
            continue
        seen.add(name)
        kernels.append({'name': name, 'kind': kind, 'manifest': _peek_manifest(name)})
    return kernels


def _suggest(name: str) -> list[str]:
    """Return close matches for a mistyped kernel name."""
    all_names = [k['name'] for k in _list_kernels()]
    return difflib.get_close_matches(name, all_names, n=3, cutoff=0.5)


def _peek_manifest(extractor_name: str) -> dict:
    """Read MANIFEST via AST (no code execution). Returns {} on any failure."""
    import ast
    edir = _extractors_dir()
    json_path = os.path.join(edir, f"{extractor_name}.json")
    py_path   = os.path.join(edir, f"{extractor_name}.py")

    try:
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                return json.load(f)

        if os.path.exists(py_path):
            with open(py_path, 'r', encoding='utf-8') as f:
                source = f.read()
            tree = ast.parse(source)
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        if isinstance(t, ast.Name) and t.id == 'MANIFEST':
                            return ast.literal_eval(node.value)
    except Exception:
        pass
    return {}


def _load_kernel(extractor_name: str) -> tuple:
    """
    Load and return (adapter, manifest, load_error).
    load_error is a string describing the failure, or None on success.
    Never raises.
    """
    import importlib.util
    edir      = _extractors_dir()
    json_path = os.path.join(edir, f"{extractor_name}.json")
    py_path   = os.path.join(edir, f"{extractor_name}.py")

    try:
        if os.path.exists(json_path):
            from core.extractors.base import SubprocessExtractorAdapter
            with open(json_path, 'r', encoding='utf-8') as f:
                manifest = json.load(f)
            adapter = SubprocessExtractorAdapter(
                extractor_name,
                manifest.get('command', []),
                description=manifest.get('description', '')
            )
            adapter.__name__ = extractor_name
            return adapter, manifest, None

        if os.path.exists(py_path):
            spec = importlib.util.spec_from_file_location(extractor_name, py_path)
            mod  = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
            except ImportError as e:
                return None, {}, (
                    f"ImportError loading {extractor_name}: {e}\n"
                    f"Install the missing package and retry."
                )
            except Exception as e:
                return None, {}, (
                    f"Error executing module {extractor_name}: {e}\n"
                    + _tb_mod.format_exc()
                )
            mod.__name__ = extractor_name
            manifest     = getattr(mod, 'MANIFEST', {})
            return mod, manifest, None

        # Not found — give a helpful suggestion
        suggestions = _suggest(extractor_name)
        hint = ''
        if suggestions:
            hint = f"  Did you mean: {', '.join(suggestions)}?"
        return None, {}, f"Kernel not found: {extractor_name}.{hint}"

    except Exception as e:
        return None, {}, f"Unexpected error loading kernel: {e}\n{_tb_mod.format_exc()}"


def _safe_settings_resolver(vault_id=None):
    """Return a SettingsResolver, or a no-op fallback if the DB isn't available."""
    try:
        from core.settings import SettingsResolver
        return SettingsResolver(vault_id=vault_id)
    except Exception:
        # DB not initialised — return a minimal object that returns schema defaults
        class _FallbackResolver:
            def get(self, key):
                try:
                    from core.settings import settings
                    return settings.schema.get(key, {}).get('default')
                except Exception:
                    return None
        return _FallbackResolver()


# ── Contract audit ────────────────────────────────────────────────────────────

def run_audit(extractor_name: str) -> dict:
    """
    Static contract checks (manifest + signatures).
    Returns {'passed': bool, 'checks': [{id, passed, message}]}.
    """
    result = {'kernel': extractor_name, 'passed': False, 'checks': []}

    mod, manifest, load_err = _load_kernel(extractor_name)
    if load_err:
        result['checks'].append({'id': 'load', 'passed': False, 'message': load_err})
        return result

    try:
        from core.certification import ContractAuditor
        import dataclasses as _dc
        auditor = ContractAuditor()
        for check in (auditor.verify_manifest(mod), auditor.verify_signatures(mod)):
            result['checks'].append(_dc.asdict(check))
        result['passed'] = all(c['passed'] for c in result['checks'])
    except Exception as e:
        result['checks'].append({'id': 'audit', 'passed': False,
                                  'message': f"Audit system error: {e}"})
    return result


# ── Core test runner ──────────────────────────────────────────────────────────

def run_test(extractor_name: str, file_path: str,
             timeout_secs: int = 300,
             vault_id: str = None,
             text_limit: int = 500,
             skip_audit: bool = False) -> dict:
    """
    Run one kernel against one file. Returns a result dict.
    All errors are captured; this function itself never raises.
    """
    file_path = os.path.normpath(file_path)

    result = {
        'kernel':        extractor_name,
        'file':          file_path,
        'file_size':     None,
        'file_ext':      os.path.splitext(file_path)[1].lstrip('.').lower() or '(none)',
        'status':        'unknown',
        'elapsed_secs':  0.0,
        'load_secs':     0.0,
        'text_chars':    0,
        'text_lines':    0,
        'text_preview':  None,
        'full_text':     None,
        'metadata':      {},
        'image_count':   0,
        'child_tasks':   0,
        'error_count':   0,
        'errors':        [],
        'traceback':     None,
        'manifest':      _peek_manifest(extractor_name),
        'audit':         None,
        'notes':         [],
    }

    # ── File validation ───────────────────────────────────────────────────────
    if not os.path.exists(file_path):
        result['status'] = 'error'
        result['errors'] = [f"File not found: {file_path}"]
        return result

    try:
        result['file_size'] = os.path.getsize(file_path)
    except OSError:
        pass

    # ── Optional pre-flight audit ─────────────────────────────────────────────
    if not skip_audit:
        audit = run_audit(extractor_name)
        result['audit'] = audit
        if not audit['passed']:
            failing = [c['message'] for c in audit['checks'] if not c['passed']]
            result['status'] = 'audit_fail'
            result['errors'] = failing
            return result

    # ── Kernel load ───────────────────────────────────────────────────────────
    load_start = time.monotonic()
    mod, manifest, load_err = _load_kernel(extractor_name)
    result['load_secs'] = round(time.monotonic() - load_start, 3)

    if load_err:
        result['status'] = 'error'
        result['errors'] = [load_err]
        return result

    if result['load_secs'] > 1.0:
        result['notes'].append(
            f"Kernel took {result['load_secs']}s to load — "
            "heavy dependencies (torch/whisper/mediapipe?) may be initialising."
        )

    # ── Build context ─────────────────────────────────────────────────────────
    try:
        from core.extractors.base import (
            LegacyExtractorAdapter, ExtractorContext, ExtractorLogger, BaseExtractor
        )
        cancel_token = threading.Event()
        dummy_hash   = f"CLI_{int(time.time() * 1000)}"
        ext_logger   = ExtractorLogger(extractor_name, vault_id or 'CLI', dummy_hash)
        ctx = ExtractorContext(
            vault_id     = vault_id or 'CLI',
            file_hash    = dummy_hash,
            cancel_token = cancel_token,
            logger       = ext_logger,
            settings     = _safe_settings_resolver(vault_id),
        )
        adapter = mod if isinstance(mod, BaseExtractor) else LegacyExtractorAdapter(mod)
    except Exception as e:
        result['status'] = 'error'
        result['errors'] = [f"Context setup failed: {e}"]
        result['traceback'] = _tb_mod.format_exc()
        return result

    # ── Execute in thread with timeout ────────────────────────────────────────
    result_box = {}
    error_box  = {}

    def _run():
        try:
            result_box['v'] = adapter.run(file_path, ctx)
        except Exception as exc:
            error_box['exc'] = str(exc)
            error_box['tb']  = _tb_mod.format_exc()

    t = threading.Thread(target=_run, daemon=True, name=f'kernel-{extractor_name}')
    wall_start = time.monotonic()
    t.start()
    t.join(timeout=timeout_secs)
    wall_elapsed = round(time.monotonic() - wall_start, 3)

    if t.is_alive():
        cancel_token.set()
        result['status']       = 'timeout'
        result['elapsed_secs'] = wall_elapsed
        result['errors']       = [
            f"Timed out after {timeout_secs}s. "
            "If an AI model is loading for the first time, try again with a larger --timeout."
        ]
        return result

    result['elapsed_secs'] = wall_elapsed

    if 'exc' in error_box:
        result['status']    = 'crash'
        result['errors']    = [error_box['exc']]
        result['traceback'] = error_box['tb']
        return result

    # ── Unpack IngestResult ───────────────────────────────────────────────────
    ingest = result_box['v']

    if ingest.text:
        result['text_chars']   = len(ingest.text)
        result['text_lines']   = ingest.text.count('\n') + 1
        result['text_preview'] = ingest.text[:text_limit]
        result['full_text']    = ingest.text   # caller strips if not wanted

    result['metadata']    = ingest.metadata or {}
    result['image_count'] = len(ingest.images)
    result['child_tasks'] = len(ingest.child_tasks)
    result['error_count'] = len(ingest.errors)
    result['errors']      = [
        {'extractor': e.extractor_name, 'type': e.error_type, 'message': e.message}
        for e in ingest.errors
    ]

    # Annotate slow runs
    if ingest.elapsed_secs > 30:
        result['notes'].append(f"Slow extraction: {ingest.elapsed_secs:.1f}s")

    # Determine pass/fail
    has_output = bool(ingest.text or ingest.metadata or ingest.images or ingest.child_tasks)
    if ingest.status == 'cancelled':
        result['status'] = 'fail'
        result['notes'].append("Extraction was cancelled before completion.")
    elif ingest.status == 'failed' or (ingest.errors and not has_output):
        result['status'] = 'fail'
    else:
        result['status'] = 'pass'

    return result


# ── Formatters ────────────────────────────────────────────────────────────────

STATUS_COLOR = {
    'pass':       GRN,
    'fail':       RED,
    'timeout':    YLW,
    'crash':      RED,
    'error':      RED,
    'audit_fail': MAG,
    'unknown':    DIM,
}

def fmt_json(result: dict, include_full_text: bool = False) -> str:
    out = {k: v for k, v in result.items() if k != 'full_text'}
    if include_full_text:
        out['full_text'] = result.get('full_text')
    return json.dumps(out, indent=2, ensure_ascii=False, default=str)


def fmt_pretty(result: dict, include_full_text: bool = False) -> str:
    W   = 64
    col = STATUS_COLOR.get(result['status'], DIM)
    sep = '═' * W

    lines = [f"\n{sep}"]
    lines.append(f"  {col(result['status'].upper()):<12}  {BOLD(result['kernel'])}")
    lines.append(sep)

    # File info
    size_str = _fmt_size(result['file_size']) if result['file_size'] else '?'
    lines.append(f"  {DIM('File')}     {result['file']}  {DIM(size_str)}")
    lines.append(f"  {DIM('Elapsed')}  {result['elapsed_secs']}s  "
                 f"{DIM('(load: ' + str(result['load_secs']) + 's)'  if result['load_secs'] else '')}")

    # Output summary
    if result['text_chars']:
        lines.append(f"  {DIM('Text')}     {result['text_chars']:,} chars  "
                     f"{DIM(str(result['text_lines']) + ' lines')}")
    if result['image_count']:
        lines.append(f"  {DIM('Images')}   {result['image_count']}")
    if result['child_tasks']:
        lines.append(f"  {DIM('Children')} {result['child_tasks']} tasks queued")
    if result['metadata']:
        lines.append(f"  {DIM('Metadata')} {json.dumps(result['metadata'], default=str)}")

    # Audit
    if result.get('audit'):
        audit = result['audit']
        tag   = GRN('AUDIT OK') if audit['passed'] else RED('AUDIT FAIL')
        lines.append(f"\n  {tag}")
        for c in audit.get('checks', []):
            sym = GRN('✓') if c['passed'] else RED('✗')
            cid = c.get('check_id') or c.get('id', '?')
            lines.append(f"    {sym}  {cid:<14}  {c['message']}")

    # Notes
    if result['notes']:
        lines.append(f"\n  {YLW('NOTES')}")
        for n in result['notes']:
            lines.append(f"    ▸  {n}")

    # Errors
    if result['errors']:
        lines.append(f"\n  {RED('ERRORS')}  ({result['error_count']})")
        for e in result['errors']:
            if isinstance(e, dict):
                lines.append(f"    [{e.get('type','?')}]  {e.get('message','')}")
            else:
                lines.append(f"    {e}")

    if result.get('traceback'):
        lines.append(f"\n  {RED('TRACEBACK')}")
        for tline in result['traceback'].splitlines()[-20:]:
            lines.append(f"  {DIM(tline)}")

    # Text preview
    preview_text = result.get('full_text') if include_full_text else result.get('text_preview')
    if preview_text:
        label = 'FULL TEXT' if include_full_text else f'TEXT PREVIEW (first {len(result["text_preview"])} chars)'
        lines.append(f"\n  {BLU(label)}")
        lines.append(f"  {'─' * (W - 2)}")
        for line in preview_text.splitlines()[:200 if include_full_text else 25]:
            lines.append(f"  {line}")
        if not include_full_text and result['text_chars'] > len(result.get('text_preview', '')):
            remaining = result['text_chars'] - len(result.get('text_preview', ''))
            lines.append(f"  {DIM(f'... {remaining:,} more chars (use --full-text to see all)')}")

    lines.append(f"{sep}\n")
    return '\n'.join(lines)


def _fmt_size(n: int) -> str:
    if n < 1024:       return f"{n} B"
    if n < 1_048_576:  return f"{n/1024:.1f} KB"
    return f"{n/1_048_576:.1f} MB"


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog='test_kernel',
        description='DocVault standalone kernel test adapter.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('kernel',     nargs='?',     help='Extractor module name')
    parser.add_argument('files',      nargs='*',     help='File(s) to test against')
    parser.add_argument('--timeout',  type=int,    default=300, metavar='SECS')
    parser.add_argument('--vault',    default=None, metavar='VAULT_ID')
    parser.add_argument('--no-text',  action='store_true', help='Omit text from output')
    parser.add_argument('--full-text',action='store_true', help='Include full extracted text')
    parser.add_argument('--text-limit', type=int, default=500, metavar='N',
                        help='Preview char limit when not using --full-text (default 500)')
    parser.add_argument('--pretty',   action='store_true', help='Human-readable output')
    parser.add_argument('--list',     action='store_true', help='List all kernels and exit')
    parser.add_argument('--info',     action='store_true', help='Show manifest only, no run')
    parser.add_argument('--audit',    action='store_true', help='Run contract audit only')
    parser.add_argument('--skip-audit', action='store_true',
                        help='Skip pre-flight audit and run directly')
    parser.add_argument('--output',   default=None, metavar='FILE',
                        help='Write JSON result to file (in addition to stdout)')
    parser.add_argument('--quiet',    action='store_true',
                        help='Suppress spinner and progress output')

    args = parser.parse_args()

    # ── --list ────────────────────────────────────────────────────────────────
    if args.list:
        kernels = _list_kernels()
        if args.pretty:
            print(f"\n{BOLD(str(len(kernels)))} kernel(s) discoverable in extractors/\n")
            for k in kernels:
                m   = k['manifest']
                exts = ', '.join(str(e) for e in m.get('extensions', [])[:10])
                tag  = DIM('subprocess') if k['kind'] == 'subprocess' else ''
                print(f"  {k['name']:<52} {DIM('v' + str(m.get('version','?'))):<10}  [{exts}]  {tag}")
            print()
        else:
            print(json.dumps(kernels, indent=2, ensure_ascii=False))
        sys.exit(0)

    # ── kernel required beyond this point ─────────────────────────────────────
    if not args.kernel:
        parser.print_help()
        sys.exit(4)

    # Strip accidental .py suffix
    kernel_name = args.kernel
    if kernel_name.endswith('.py'):
        kernel_name = kernel_name[:-3]
        print(f"{YLW('Note:')} stripped .py suffix → using '{kernel_name}'", file=sys.stderr)

    # ── --info ────────────────────────────────────────────────────────────────
    if args.info:
        m = _peek_manifest(kernel_name)
        if args.pretty:
            print(f"\n{BOLD('Kernel')}  {kernel_name}\n")
            for k, v in m.items():
                print(f"  {DIM(k):<20} {v}")
            print()
        else:
            print(json.dumps({'kernel': kernel_name, 'manifest': m}, indent=2))
        sys.exit(0)

    # ── --audit ───────────────────────────────────────────────────────────────
    if args.audit:
        audit = run_audit(kernel_name)
        if args.pretty:
            tag = GRN('PASSED') if audit['passed'] else RED('FAILED')
            print(f"\nContract Audit: {BOLD(kernel_name)}  →  {tag}\n")
            for c in audit['checks']:
                sym = GRN('✓') if c['passed'] else RED('✗')
                cid = c.get('check_id') or c.get('id', '?')
                print(f"  {sym}  {cid:<16} {c['message']}")
            print()
        else:
            print(json.dumps(audit, indent=2))
        sys.exit(0 if audit['passed'] else 5)

    # ── run test ──────────────────────────────────────────────────────────────
    if not args.files:
        _die(4, f"Provide at least one file to test against.\n"
                f"Usage: python tools/test_kernel.py {kernel_name} <file> [<file2> ...]")

    all_results = []
    worst_code  = 0

    for file_path in args.files:
        label = f"Running {kernel_name} on {os.path.basename(file_path)}"

        if args.quiet or not sys.stderr.isatty():
            print(f"  → {label} ...", file=sys.stderr, flush=True)
            result = run_test(
                kernel_name, file_path,
                timeout_secs=args.timeout,
                vault_id=args.vault,
                text_limit=args.text_limit,
                skip_audit=args.skip_audit,
            )
        else:
            with Spinner(label):
                result = run_test(
                    kernel_name, file_path,
                    timeout_secs=args.timeout,
                    vault_id=args.vault,
                    text_limit=args.text_limit,
                    skip_audit=args.skip_audit,
                )

        # Strip text from result dict based on flags
        if args.no_text:
            result['text_preview'] = None
            result['full_text']    = None
        include_full = args.full_text and not args.no_text

        if args.pretty:
            print(fmt_pretty(result, include_full_text=include_full))
        else:
            # For a single file, print the dict directly; for multiple, collect
            if len(args.files) == 1:
                print(fmt_json(result, include_full_text=include_full))

        all_results.append(result)

        code = {'pass': 0, 'fail': 1, 'timeout': 2, 'crash': 3,
                'audit_fail': 5, 'error': 4}.get(result['status'], 4)
        worst_code = max(worst_code, code)

    # Multiple files → print JSON array
    if len(args.files) > 1 and not args.pretty:
        include_full = args.full_text and not args.no_text
        out = []
        for r in all_results:
            d = {k: v for k, v in r.items() if k != 'full_text'}
            if include_full:
                d['full_text'] = r.get('full_text')
            out.append(d)
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))

    # --output
    if args.output:
        include_full = args.full_text and not args.no_text
        payload = all_results[0] if len(all_results) == 1 else all_results
        def _strip(r):
            d = {k: v for k, v in r.items() if k != 'full_text'}
            if include_full:
                d['full_text'] = r.get('full_text')
            return d
        try:
            with open(args.output, 'w', encoding='utf-8') as f:
                if isinstance(payload, list):
                    json.dump([_strip(r) for r in payload], f, indent=2,
                              ensure_ascii=False, default=str)
                else:
                    json.dump(_strip(payload), f, indent=2,
                              ensure_ascii=False, default=str)
            print(f"{GRN('Saved')} → {args.output}", file=sys.stderr)
        except OSError as e:
            print(f"{RED('Could not write')} {args.output}: {e}", file=sys.stderr)

    sys.exit(worst_code)


def _die(code: int, message: str):
    print(f"{RED('Error:')} {message}", file=sys.stderr)
    sys.exit(code)


if __name__ == '__main__':
    main()
