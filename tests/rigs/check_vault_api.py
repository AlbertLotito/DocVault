#!/usr/bin/env python3
"""
check_vault_api.py -- Validates the vault API contract over HTTP.

Requires the DocVault server to be running at the target URL.
Tests: vault CRUD, state machine transitions, per-vault settings,
invalid transition rejection, duplicate directory rejection.

Exit codes: 0 = pass, 1 = fail

Usage:
  python tests/rigs/check_vault_api.py
  python tests/rigs/check_vault_api.py --url http://localhost:8000
  python tests/rigs/check_vault_api.py --verbose
"""
import argparse, sys, json
try:
    import urllib.request, urllib.error
except ImportError:
    pass

GREEN = '\033[92m'; RED = '\033[91m'; YELLOW = '\033[93m'; RESET = '\033[0m'
def _pass(msg): print(f"{GREEN}PASS{RESET}  {msg}"); return True
def _fail(msg): print(f"{RED}FAIL{RESET}  {msg}"); return False
def _warn(msg): print(f"{YELLOW}WARN{RESET}  {msg}")


def _req(url, method='GET', body=None, params=None):
    if params:
        url += '?' + '&'.join(f"{k}={v}" for k, v in params.items())
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(url, data=data, method=method)
    req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as e:
        return 0, {'error': str(e)}


def run_checks(base_url, verbose):
    results = []
    vapi = f"{base_url}/api/vaults"

    # --- Create vault ---
    status, body = _req(vapi, 'POST', {'name': 'RigTest', 'scan_directory': 'E:/RigTest99'})
    results.append(_pass(f"POST /api/vaults -> {status}") if status == 200
                   else _fail(f"Create vault failed: {status} {body}"))
    if status != 200:
        return results
    vault_id = body.get('vault_id')

    # --- Get vault ---
    status, body = _req(f"{vapi}/{vault_id}")
    results.append(_pass(f"GET /api/vaults/{{id}} -> {status}") if status == 200
                   else _fail(f"Get vault: {status} {body}"))

    # --- List vaults ---
    status, body = _req(vapi)
    results.append(_pass(f"GET /api/vaults -> {status}, {len(body)} vault(s)")
                   if status == 200 and isinstance(body, list)
                   else _fail(f"List vaults: {status}"))

    # --- Duplicate scan_directory rejected ---
    status, body = _req(vapi, 'POST', {'name': 'Dup', 'scan_directory': 'E:/RigTest99'})
    results.append(_pass("Duplicate scan_directory -> 409") if status == 409
                   else _fail(f"Expected 409 for duplicate dir, got {status}"))

    # --- Cannot gut active vault ---
    status, body = _req(f"{vapi}/{vault_id}", 'DELETE', params={'mode': 'gut'})
    results.append(_pass("Gut active vault -> 409") if status == 409
                   else _fail(f"Expected 409 gut active, got {status} {body}"))

    # --- Archive vault ---
    status, body = _req(f"{vapi}/{vault_id}", 'DELETE', params={'mode': 'archive'})
    results.append(_pass(f"Archive vault -> {status}, state={body.get('state')}")
                   if status == 200 and body.get('state') == 'archived'
                   else _fail(f"Archive failed: {status} {body}"))

    # --- Cannot delete archived vault (must gut first) ---
    status, body = _req(f"{vapi}/{vault_id}", 'DELETE', params={'mode': 'delete'})
    results.append(_pass("Delete archived vault -> 409") if status == 409
                   else _fail(f"Expected 409 delete archived, got {status} {body}"))

    # --- Restore vault ---
    status, body = _req(f"{vapi}/{vault_id}/restore", 'POST')
    results.append(_pass(f"Restore vault -> {status}, state={body.get('state')}")
                   if status == 200 and body.get('state') == 'active'
                   else _fail(f"Restore failed: {status} {body}"))

    # --- Per-vault settings set/get ---
    status, body = _req(
        f"{vapi}/{vault_id}/settings", 'POST',
        body={'value': '300'},
        params={'key': 'embeddings:chunk_size'}
    )
    results.append(_pass(f"Set vault setting -> {status}") if status == 200
                   else _fail(f"Set setting failed: {status} {body}"))

    status, settings_list = _req(f"{vapi}/{vault_id}/settings")
    vault_chunk = next((s for s in settings_list if s['key'] == 'embeddings:chunk_size'), None)
    results.append(_pass(f"Vault setting resolves to '300': {vault_chunk and vault_chunk.get('value')}")
                   if vault_chunk and vault_chunk.get('value') == '300'
                   else _fail(f"Setting not found or wrong value: {vault_chunk}"))

    # --- Monitor status ---
    status, body = _req(f"{base_url}/api/monitor/status")
    results.append(_pass(f"GET /api/monitor/status -> {status}, state={body.get('state')}")
                   if status == 200 and 'state' in body
                   else _fail(f"Monitor status: {status} {body}"))

    # --- Clean up: gut then delete the test vault ---
    _req(f"{vapi}/{vault_id}", 'DELETE', params={'mode': 'archive'})
    _req(f"{vapi}/{vault_id}", 'DELETE', params={'mode': 'gut'})
    _req(f"{vapi}/{vault_id}", 'DELETE', params={'mode': 'delete'})
    if verbose:
        print("       Test vault cleaned up.")

    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url',     default='http://localhost:8000',
                        help='DocVault server URL (default: http://localhost:8000)')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    print(f"\nDocVault Layer 3 -- Vault API Check ({args.url})\n")
    print("NOTE: Server must be running. Start with: python run.py\n")

    results = run_checks(args.url, args.verbose)
    failed  = sum(1 for r in results if not r)
    print(f"\n{len(results)-failed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
