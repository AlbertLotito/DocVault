"""
Regression tests for register_system_kernels() auto-certification.
"""
import os
import sys
import sqlite3
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from core import manager


@pytest.fixture
def settings_db(tmp_path, monkeypatch):
    """An isolated settings.db with ext_registry table."""
    db_path = str(tmp_path / "settings.db")
    # Patch both the source and the name already bound in registry.py at import time
    monkeypatch.setattr('core.manager.get_settings_db_path', lambda: db_path)
    monkeypatch.setattr('core.registry.get_settings_db_path', lambda: db_path)
    manager.init_settings_db()
    return db_path


def _insert_kernel(db_path, kernel_id, status='unverified', certified_at=None):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO ext_registry
               (kernel_id, module_name, version, file_hash, extensions, status,
                kernel_type, target_type, description, is_enabled, certified_at)
               VALUES (?, ?, '1.0.0', 'abc123', '["zip"]', ?, 'python', 'file', '', 0, ?)""",
            (kernel_id, kernel_id.split('.')[-1], status, certified_at)
        )
        conn.commit()


def _get_status(db_path, kernel_id):
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT status, is_enabled, certified_at FROM ext_registry WHERE kernel_id = ?",
            (kernel_id,)
        ).fetchone()
    return row


def test_register_system_kernels_certifies_uncertified_docvault_kernel(settings_db, monkeypatch):
    """A com.docvault.system.* kernel with certified_at=NULL is auto-certified."""
    _insert_kernel(settings_db, 'com.docvault.system.archive_xray')

    # settings_db fixture already patches get_settings_db_path; patch sync_disk_to_db to no-op
    from core.registry import RegistryManager
    rm = RegistryManager()
    monkeypatch.setattr(rm, 'sync_disk_to_db', lambda: None)

    rm.register_system_kernels()

    status, enabled, certified_at = _get_status(settings_db, 'com.docvault.system.archive_xray')
    assert status == 'certified'
    assert enabled == 1
    assert certified_at is not None


def test_register_system_kernels_is_idempotent(settings_db, monkeypatch):
    """Calling register_system_kernels() twice does not overwrite certified_at."""
    _insert_kernel(settings_db, 'com.docvault.system.archive_xray')

    from core.registry import RegistryManager
    rm = RegistryManager()
    monkeypatch.setattr(rm, 'sync_disk_to_db', lambda: None)

    rm.register_system_kernels()
    _, _, first_certified_at = _get_status(settings_db, 'com.docvault.system.archive_xray')

    rm.register_system_kernels()
    _, _, second_certified_at = _get_status(settings_db, 'com.docvault.system.archive_xray')

    # WHERE certified_at IS NULL excludes already-certified rows → timestamp unchanged
    assert first_certified_at == second_certified_at


def test_register_system_kernels_does_not_certify_third_party(settings_db, monkeypatch):
    """A com.thirdparty.* kernel is NOT auto-certified."""
    _insert_kernel(settings_db, 'com.thirdparty.foo')

    from core.registry import RegistryManager
    rm = RegistryManager()
    monkeypatch.setattr(rm, 'sync_disk_to_db', lambda: None)

    rm.register_system_kernels()

    status, enabled, certified_at = _get_status(settings_db, 'com.thirdparty.foo')
    assert status == 'unverified'
    assert enabled == 0
    assert certified_at is None
