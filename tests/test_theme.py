import json
import pytest
from fastapi.testclient import TestClient


# ── Schema tests ────────────────────────────────────────────────────────────

def test_ui_theme_in_schema():
    from core.settings import settings
    assert 'ui:theme' in settings.schema
    entry = settings.schema['ui:theme']
    assert entry['type'] == 'string'
    assert entry['default'] == '{}'
    assert entry['group'] == 'ui'
    assert entry.get('hidden') is True


def test_hidden_keys_not_in_configurable():
    from core.settings import settings
    items = settings.get_all_configurable()
    keys = [s['key'] for s in items]
    # ui:theme is hidden — must not appear in the configurable list
    assert 'ui:theme' not in keys
    # ui:language is not hidden — must still appear
    assert 'ui:language' in keys


# ── API endpoint tests ────────────────────────────────────────────────────────

def test_get_theme_returns_empty_dict_by_default():
    from api.main import app
    client = TestClient(app)
    resp = client.get('/api/settings/theme')
    assert resp.status_code == 200
    data = resp.json()
    assert 'theme' in data
    assert data['theme'] == {}


def test_get_theme_returns_saved_values(monkeypatch):
    from core.settings import settings as s
    # Temporarily patch settings.get to return a known JSON blob
    saved = {'--c-accent': '#0088ff', '--c-bg': '#000814'}
    monkeypatch.setattr(s, 'get', lambda key: json.dumps(saved) if key == 'ui:theme' else None)
    from api.main import app
    client = TestClient(app)
    resp = client.get('/api/settings/theme')
    assert resp.status_code == 200
    assert resp.json()['theme'] == saved


def test_get_theme_returns_empty_on_corrupt_json(monkeypatch):
    from core.settings import settings as s
    monkeypatch.setattr(s, 'get', lambda key: 'NOT_JSON' if key == 'ui:theme' else None)
    from api.main import app
    client = TestClient(app)
    resp = client.get('/api/settings/theme')
    assert resp.status_code == 200
    assert resp.json()['theme'] == {}
