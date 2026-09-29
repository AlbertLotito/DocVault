"""
Tests for the Browse page backend:
  GET  /api/catalog/tree              — one level of a vault's folder tree
  POST /api/catalog/{hash}/scan_now   — jump an unprocessed file to the front of the extraction queue
  GET  /browse                        — the page itself
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core import manager
from core.vault_manager import VaultManager


def _add(db, vault_id, path, file_hash, status='PENDING', priority=10):
    manager.insert_task(db, file_hash, path, os.path.splitext(path)[1].lstrip('.'),
                        priority=priority, file_size=1234, vault_id=vault_id)
    manager.upsert_file_vault(db, file_hash, vault_id, path)
    if status != 'PENDING':
        manager.update_task_status(db, file_hash, status=status)


@pytest.fixture
def env(tmp_path):
    from api.main import app
    db = str(tmp_path / 'test.db')
    manager.init_db(db)
    root = tmp_path / 'vault'
    other_root = tmp_path / 'other'
    vm = VaultManager(db)
    # Stored with forward slashes on purpose — real vaults are saved that way
    # (e.g. 'Z:/Documents') while file paths use the OS separator.
    vault = vm.create_vault('Docs', str(root).replace('\\', '/'))
    other = vm.create_vault('Other', str(other_root))
    vid = vault['vault_id']

    j = os.path.join
    _add(db, vid, j(root, 'readme.txt'),               'h_readme', 'COMPLETED')
    _add(db, vid, j(root, 'Letters', 'a.docx'),        'h_a',      'COMPLETED')
    _add(db, vid, j(root, 'Letters', 'b.docx'),        'h_b',      'ERROR')
    _add(db, vid, j(root, 'Letters', 'Old', 'c.pdf'),  'h_c',      'PENDING')
    _add(db, vid, j(root, 'a_b', 'x.txt'),             'h_ab',     'COMPLETED')
    _add(db, vid, j(root, 'axb', 'y.txt'),             'h_axb',    'COMPLETED')
    _add(db, other['vault_id'], j(other_root, 'z.txt'), 'h_other', 'COMPLETED')

    with patch('api.main.DB_PATH', db):
        yield TestClient(app), db, vid


# ── GET /api/catalog/tree ─────────────────────────────────────────────────────

def test_tree_root_lists_top_level_folders_and_direct_files(env):
    tc, _, vid = env
    resp = tc.get(f'/api/catalog/tree?vault_id={vid}')
    assert resp.status_code == 200
    data = resp.json()

    folders = {f['name']: f for f in data['folders']}
    assert set(folders) == {'Letters', 'a_b', 'axb'}
    letters = folders['Letters']
    assert letters['total'] == 3                      # recursive: a, b, Old/c
    assert letters['by_status'] == {'COMPLETED': 1, 'ERROR': 1, 'PENDING': 1}
    assert letters['path'] == 'Letters'

    assert [f['name'] for f in data['files']] == ['readme.txt']
    readme = data['files'][0]
    assert readme['file_hash'] == 'h_readme'
    assert readme['status'] == 'COMPLETED'
    assert readme['file_size'] == 1234


def test_tree_subfolder_lists_its_own_children(env):
    tc, _, vid = env
    data = tc.get(f'/api/catalog/tree?vault_id={vid}&path=Letters').json()
    assert [f['name'] for f in data['folders']] == ['Old']
    assert data['folders'][0]['path'] == os.path.join('Letters', 'Old')
    assert [f['name'] for f in data['files']] == ['a.docx', 'b.docx']


def test_tree_nested_path_round_trips(env):
    tc, _, vid = env
    nested = os.path.join('Letters', 'Old')
    data = tc.get('/api/catalog/tree', params={'vault_id': vid, 'path': nested}).json()
    assert data['folders'] == []
    assert [f['name'] for f in data['files']] == ['c.pdf']


def test_tree_wildcard_characters_in_folder_names_match_literally(env):
    tc, _, vid = env
    data = tc.get(f'/api/catalog/tree?vault_id={vid}&path=a_b').json()
    assert [f['name'] for f in data['files']] == ['x.txt']   # not axb/y.txt


def test_tree_excludes_other_vaults(env):
    tc, _, vid = env
    data = tc.get(f'/api/catalog/tree?vault_id={vid}').json()
    names = [f['name'] for f in data['files']] + [f['name'] for f in data['folders']]
    assert 'z.txt' not in names


def test_tree_unknown_vault_is_404(env):
    tc, _, _ = env
    assert tc.get('/api/catalog/tree?vault_id=nope').status_code == 404


@pytest.mark.parametrize('bad', ['..', os.path.join('Letters', '..', '..'), os.path.abspath(os.sep)])
def test_tree_rejects_paths_outside_the_vault(env, bad):
    tc, _, vid = env
    resp = tc.get('/api/catalog/tree', params={'vault_id': vid, 'path': bad})
    assert resp.status_code == 400


# ── POST /api/catalog/{hash}/scan_now ─────────────────────────────────────────

def test_scan_now_pending_file_is_claimed_next(env):
    tc, db, vid = env
    # h_c is PENDING at the default priority; give another file a higher one.
    _add(db, vid, os.path.join('elsewhere', 'urgent.pdf'), 'h_urgent', 'PENDING', priority=90)

    resp = tc.post('/api/catalog/h_c/scan_now')
    assert resp.status_code == 200

    claimed = manager.claim_pending_task(db, 'w1')
    assert claimed['file_hash'] == 'h_c'


def test_scan_now_error_file_is_requeued_and_claimed_next(env):
    tc, db, _ = env
    resp = tc.post('/api/catalog/h_b/scan_now')
    assert resp.status_code == 200

    task = manager.get_task(db, 'h_b')
    assert task['status'] == 'PENDING'
    assert not task['error_log']
    assert manager.claim_pending_task(db, 'w1')['file_hash'] == 'h_b'


def test_scan_now_rejects_already_processed_file(env):
    tc, db, _ = env
    resp = tc.post('/api/catalog/h_a/scan_now')
    assert resp.status_code == 409
    assert manager.get_task(db, 'h_a')['status'] == 'COMPLETED'


def test_scan_now_unknown_file_is_404(env):
    tc, _, _ = env
    assert tc.post('/api/catalog/nope/scan_now').status_code == 404


# ── Page + nav wiring ─────────────────────────────────────────────────────────

def test_browse_page_is_served(env):
    tc, _, _ = env
    resp = tc.get('/browse')
    assert resp.status_code == 200
    assert 'data-page="browse"' in resp.text


def test_browse_is_in_the_nav():
    js = open(os.path.join(os.path.dirname(__file__), '..', 'frontend', 'static', 'lcars.js'),
              encoding='utf-8').read()
    assert 'href="/browse"' in js
    assert "t('nav.browse')" in js
