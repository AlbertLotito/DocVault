import json
import os
import pytest

I18N_DIR = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'static', 'i18n')


def test_manifest_exists_and_valid():
    path = os.path.join(I18N_DIR, 'manifest.json')
    assert os.path.exists(path), "manifest.json not found"
    with open(path) as f:
        data = json.load(f)
    assert isinstance(data, list)
    assert len(data) >= 3
    codes = [e['code'] for e in data]
    assert 'en' in codes
    assert 'es' in codes
    assert 'fr' in codes


def test_en_json_exists_and_valid():
    path = os.path.join(I18N_DIR, 'en.json')
    assert os.path.exists(path), "en.json not found"
    with open(path) as f:
        data = json.load(f)
    assert isinstance(data, dict)
    assert len(data) > 50, "en.json should have at least 50 keys"
    for key in ['nav.search', 'nav.vault', 'sensor.cpu', 'common.save',
                'vault.header.title', 'search.heading', 'settings.heading']:
        assert key in data, f"Missing required key: {key}"


def test_es_and_fr_match_en_keys():
    en_path = os.path.join(I18N_DIR, 'en.json')
    with open(en_path) as f:
        en = json.load(f)
    for lang in ['es', 'fr']:
        path = os.path.join(I18N_DIR, f'{lang}.json')
        assert os.path.exists(path), f"{lang}.json not found"
        with open(path) as f:
            data = json.load(f)
        missing = set(en.keys()) - set(data.keys())
        assert not missing, f"{lang}.json missing keys: {missing}"


def test_ui_language_setting_in_schema():
    from core.settings import settings
    schema = settings.schema
    assert 'ui:language' in schema
    entry = schema['ui:language']
    assert entry.get('type') == 'string'
    assert entry.get('default') == ''
    assert entry.get('group') == 'ui'
