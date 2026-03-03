#!/usr/bin/env python3
"""
check_settings_resolution.py -- Validates 4-tier settings resolution chain.

Checks that SettingsResolver correctly prioritises:
  vault_settings > settings.db global > config.ini > schema default

Exit codes: 0 = pass, 1 = fail

Usage:
  python tests/rigs/check_settings_resolution.py
  python tests/rigs/check_settings_resolution.py --verbose
  python tests/rigs/check_settings_resolution.py --settings-db path/to/settings.db
"""
import argparse, sqlite3, sys, os, tempfile

GREEN = '\033[92m'; RED = '\033[91m'; RESET = '\033[0m'

def _pass(msg): print(f"{GREEN}PASS{RESET}  {msg}"); return True
def _fail(msg): print(f"{RED}FAIL{RESET}  {msg}"); return False

def run_checks(settings_db_path, verbose):
    results = []

    # Inject a fresh test DB
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, 'settings.db')
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("CREATE TABLE vault_settings (vault_id TEXT, key TEXT, value TEXT, PRIMARY KEY(vault_id,key))")
        conn.execute("INSERT INTO settings VALUES ('embeddings:chunk_size','400')")
        conn.execute("INSERT INTO vault_settings VALUES ('v1','embeddings:chunk_size','200')")
        conn.commit(); conn.close()

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
        import core.manager as m
        orig = m.get_settings_db_path
        m.get_settings_db_path = lambda: db

        try:
            from importlib import reload
            import core.settings as cs
            reload(cs)
            from core.settings import SettingsResolver

            # Schema default
            r = SettingsResolver(vault_id=None)
            val = r.get('embeddings:chunk_overlap')
            results.append(
                _pass(f"Schema default: embeddings:chunk_overlap = {val}") if int(val) == 100
                else _fail(f"Schema default wrong: got {val!r}, expected 100")
            )

            # Global DB override
            val = r.get('embeddings:chunk_size')
            results.append(
                _pass(f"Global DB override: embeddings:chunk_size = {val}") if val == '400'
                else _fail(f"Global DB override wrong: got {val!r}, expected '400'")
            )

            # Vault override
            r2 = SettingsResolver(vault_id='v1')
            val = r2.get('embeddings:chunk_size')
            results.append(
                _pass(f"Vault override: embeddings:chunk_size for v1 = {val}") if val == '200'
                else _fail(f"Vault override wrong: got {val!r}, expected '200'")
            )

            # No leak to other vault
            r3 = SettingsResolver(vault_id='v2')
            val = r3.get('embeddings:chunk_size')
            results.append(
                _pass(f"No vault leak: v2 sees global '400' not v1's '200'") if val == '400'
                else _fail(f"Vault leak detected: v2 got {val!r}, expected '400'")
            )

        finally:
            m.get_settings_db_path = orig

    return results

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--settings-db', default='settings.db')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    print("\nDocVault Layer 2 -- Settings Resolution Check\n")
    results = run_checks(args.settings_db, args.verbose)
    failed = sum(1 for r in results if not r)
    print(f"\n{len(results)-failed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)

if __name__ == '__main__':
    main()
