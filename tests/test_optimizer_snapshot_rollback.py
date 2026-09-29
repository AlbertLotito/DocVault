"""Regression tests for run._rollback_optimizer_snapshot().

Both success paths used to call logger.warning, which core.logger does not
have (it exposes warn). The AttributeError was swallowed by the outer except
and reported as "optimizer snapshot rollback failed" even though the rollback
had actually worked.
"""
import sqlite3

import pytest

import run as run_module
from core import manager


@pytest.fixture
def settings_db(tmp_path, monkeypatch):
    db = tmp_path / 'settings.db'
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(manager, 'get_settings_db_path', lambda: str(db))
    return db


@pytest.fixture
def log_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(run_module.logger, 'warn', lambda msg, ext=None: calls.append(('warn', msg)))
    monkeypatch.setattr(run_module.logger, 'error', lambda msg, ext=None: calls.append(('error', msg)))
    return calls


def _put_snapshot(db, value):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO settings (key, value) VALUES ('tuning:_optimizer_snapshot', ?)", (value,))
    conn.commit()
    conn.close()


def _snapshot_exists(db):
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT 1 FROM settings WHERE key='tuning:_optimizer_snapshot'").fetchone()
    conn.close()
    return row is not None


def test_corrupt_snapshot_is_cleared_and_warned_not_reported_as_failure(settings_db, log_calls):
    _put_snapshot(settings_db, '{not json')

    run_module._rollback_optimizer_snapshot()

    assert not _snapshot_exists(settings_db)
    assert [lvl for lvl, _ in log_calls] == ['warn']
    assert 'corrupt optimizer snapshot cleared' in log_calls[0][1]


def test_restored_snapshot_is_cleared_and_warned_not_reported_as_failure(settings_db, log_calls):
    _put_snapshot(settings_db, '{}')

    run_module._rollback_optimizer_snapshot()

    assert not _snapshot_exists(settings_db)
    assert [lvl for lvl, _ in log_calls] == ['warn']
    assert 'restored settings from stale optimizer snapshot' in log_calls[0][1]


def test_no_snapshot_logs_nothing(settings_db, log_calls):
    run_module._rollback_optimizer_snapshot()

    assert log_calls == []
