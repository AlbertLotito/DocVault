import sqlite3, tempfile, os, pytest

def make_logs_db():
    """Create an in-memory logs.db and run init_logs_db() against it."""
    import unittest.mock as mock
    tmp = tempfile.mktemp(suffix='.db')
    with mock.patch('core.manager.get_logs_db_path', return_value=tmp):
        from core.manager import init_logs_db
        init_logs_db()
    return tmp

def test_system_stats_has_queue_columns():
    db = make_logs_db()
    conn = sqlite3.connect(db)
    cols = [r[1] for r in conn.execute('PRAGMA table_info(system_stats)').fetchall()]
    conn.close()
    os.unlink(db)
    assert 'extracted_queue' in cols
    assert 'embedding_queue' in cols


from unittest.mock import patch, MagicMock

def test_process_task_batch_records_timing():
    """After a successful batch, _record_timing is called once per doc with extractor='embedding'."""
    import workers.embedding_worker as ew

    task = {
        'file_hash': 'abc123',
        'file_path': '/fake/doc.txt',
        'extracted_text': 'hello world ' * 100,
        'vault_id': None,
        'file_size': 100,
    }

    mock_vs = MagicMock()
    mock_vs.upsert_batch.return_value = None

    recorded = []

    def fake_record_timing(t, extractor, elapsed):
        recorded.append((t['file_hash'], extractor, elapsed))

    with patch('workers.embedding_worker.embedder.embed_batch', return_value=[[0.1]*384]):
        with patch('workers.embedding_worker.manager.update_task_status'):
            with patch('workers.embedding_worker.manager.update_task_progress'):
                with patch('workers.embedding_worker._record_timing', side_effect=fake_record_timing):
                    ew.process_task_batch('/fake/db', [task], mock_vs)

    assert len(recorded) == 1
    assert recorded[0][0] == 'abc123'
    assert recorded[0][1] == 'embedding'
    assert recorded[0][2] > 0


import threading, time
from datetime import datetime, timezone, timedelta


def _make_monitor():
    """Return a HardwareMonitor instance without starting its loop."""
    from core.monitor import HardwareMonitor
    with patch('core.monitor.PSUtilSensor') as m1, \
         patch('core.monitor.NvidiaSensor') as m2, \
         patch('core.monitor.WMICPUTempSensor') as m3, \
         patch('core.monitor.DiskSensor') as m4:
        for m in (m1, m2, m3, m4):
            inst = m.return_value
            inst.initialise.return_value = False
        mon = HardwareMonitor()
    return mon


def test_embed_stall_no_extracted_queue():
    """No EXTRACTED tasks → no stall."""
    from core import monitor as mon_mod
    mon_mod._set_embed_stall_state(False, 0)
    monitor = _make_monitor()

    # create=True because _connect is imported locally inside _check_embed_stall
    with patch('core.manager._connect') as mock_conn:
        mock_conn.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = {'n': 0}
        monitor._check_embed_stall()

    stalled, mins = mon_mod.get_embed_stall_state()
    assert stalled is False
    assert mins == 0


def test_embed_stall_recent_completion():
    """EXTRACTED tasks exist + recent embedding completion → no stall."""
    from core import monitor as mon_mod
    mon_mod._set_embed_stall_state(False, 0)
    monitor = _make_monitor()

    recent = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

    call_count = [0]
    def fake_connect(path):
        ctx = MagicMock()
        row = MagicMock()
        call_count[0] += 1
        if call_count[0] == 1:
            # docvault.db: EXTRACTED count
            row.__getitem__ = lambda s, k: 5
            ctx.__enter__.return_value.execute.return_value.fetchone.return_value = row
        else:
            # logs.db: last embedding completion
            row.__getitem__ = lambda s, k: recent
            ctx.__enter__.return_value.execute.return_value.fetchone.return_value = row
        return ctx

    # Patch _connect with create=True (locally imported); patch the real settings object's get method
    with patch('core.manager._connect', side_effect=fake_connect), \
         patch.object(__import__('core.settings', fromlist=['settings']).settings, 'get', return_value='5'):
        monitor._check_embed_stall()

    stalled, _ = mon_mod.get_embed_stall_state()
    assert stalled is False


def test_embed_stall_stale_completion():
    """EXTRACTED tasks exist + stale embedding completion → stall fires."""
    from core import monitor as mon_mod
    mon_mod._set_embed_stall_state(False, 0)
    monitor = _make_monitor()

    stale = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()

    call_count = [0]
    def fake_connect(path):
        ctx = MagicMock()
        row = MagicMock()
        call_count[0] += 1
        if call_count[0] == 1:
            row.__getitem__ = lambda s, k: 10
            ctx.__enter__.return_value.execute.return_value.fetchone.return_value = row
        else:
            row.__getitem__ = lambda s, k: stale
            ctx.__enter__.return_value.execute.return_value.fetchone.return_value = row
        return ctx

    with patch('core.manager._connect', side_effect=fake_connect), \
         patch.object(__import__('core.settings', fromlist=['settings']).settings, 'get', return_value='5'), \
         patch('core.alerts.send_alert', create=True):
        monitor._check_embed_stall()

    stalled, mins = mon_mod.get_embed_stall_state()
    assert stalled is True
    assert mins >= 5


def test_embed_stall_no_history():
    """No embedding history ever → stall suppressed (matches _check_stall behavior)."""
    from core import monitor as mon_mod
    mon_mod._set_embed_stall_state(False, 0)
    monitor = _make_monitor()

    call_count = [0]
    def fake_connect(path):
        ctx = MagicMock()
        row = MagicMock()
        call_count[0] += 1
        if call_count[0] == 1:
            row.__getitem__ = lambda s, k: 5  # EXTRACTED count
            ctx.__enter__.return_value.execute.return_value.fetchone.return_value = row
        else:
            row.__getitem__ = lambda s, k: None  # no completions
            ctx.__enter__.return_value.execute.return_value.fetchone.return_value = row
        return ctx

    with patch('core.manager._connect', side_effect=fake_connect), \
         patch.object(__import__('core.settings', fromlist=['settings']).settings, 'get', return_value='5'):
        monitor._check_embed_stall()

    stalled, _ = mon_mod.get_embed_stall_state()
    assert stalled is False


def test_embed_stall_clears_when_queue_empty():
    """Stall clears when EXTRACTED queue drains to zero."""
    from core import monitor as mon_mod
    mon_mod._set_embed_stall_state(True, 10)
    monitor = _make_monitor()

    def fake_connect(path):
        ctx = MagicMock()
        row = MagicMock()
        row.__getitem__ = lambda s, k: 0  # empty queue
        ctx.__enter__.return_value.execute.return_value.fetchone.return_value = row
        return ctx

    with patch('core.manager._connect', side_effect=fake_connect):
        monitor._check_embed_stall()

    stalled, _ = mon_mod.get_embed_stall_state()
    assert stalled is False


# Task 4: queue counts in _record_sample()
import sqlite3 as _sqlite3_t4, tempfile as _tempfile_t4

def test_record_sample_stores_queue_counts():
    """_record_sample writes extracted_queue and embedding_queue to system_stats."""
    import unittest.mock as mock
    from core.monitor import MonitorReading, _record_sample

    reading = MonitorReading(
        sampled_at='2026-01-01T00:00:00',
        cpu_pct=10, cpu_temp=40,
        ram_used_gb=2, ram_total_gb=16, ram_pct=12,
        gpu_temp=0, gpu_util_pct=0,
        vram_used_gb=0, vram_total_gb=0,
        disk_free_gb=100, disk_free_pct=50,
    )

    tmp_logs = _tempfile_t4.mktemp(suffix='.db')
    tmp_tasks = _tempfile_t4.mktemp(suffix='.db')

    # Bootstrap system_stats table (with new columns)
    conn = _sqlite3_t4.connect(tmp_logs)
    conn.execute("""CREATE TABLE system_stats (
        sampled_at TEXT PRIMARY KEY, cpu_pct REAL, cpu_temp REAL,
        ram_used_gb REAL, ram_total_gb REAL, ram_pct REAL,
        gpu_temp REAL, gpu_util_pct REAL, vram_used_gb REAL, vram_total_gb REAL,
        disk_free_gb REAL, disk_free_pct REAL, throttle_state TEXT,
        extracted_queue INTEGER, embedding_queue INTEGER
    )""")
    conn.commit(); conn.close()

    # Bootstrap tasks table
    conn2 = _sqlite3_t4.connect(tmp_tasks)
    conn2.execute("CREATE TABLE tasks (file_hash TEXT, status TEXT)")
    conn2.execute("INSERT INTO tasks VALUES ('h1','EXTRACTED')")
    conn2.execute("INSERT INTO tasks VALUES ('h2','EXTRACTED')")
    conn2.execute("INSERT INTO tasks VALUES ('h3','EMBEDDING')")
    conn2.commit(); conn2.close()

    with mock.patch('core.manager.get_logs_db_path', return_value=tmp_logs), \
         mock.patch('core.manager.get_db_path', return_value=tmp_tasks):
        _record_sample(reading, 'normal')

    conn = _sqlite3_t4.connect(tmp_logs)
    row = conn.execute("SELECT extracted_queue, embedding_queue FROM system_stats").fetchone()
    conn.close()

    import os; os.unlink(tmp_logs); os.unlink(tmp_tasks)
    assert row[0] == 2   # extracted_queue
    assert row[1] == 1   # embedding_queue
