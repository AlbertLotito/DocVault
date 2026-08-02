"""
Tests for the dynamic, DB-backed kernel router (core/router.py).

Routing is built at runtime from the ext_registry table (via RegistryManager),
not a static dict — so each test seeds ext_registry with certified, enabled
kernel rows, then calls router.reload(sync_disk=False) to rebuild ROUTES from
just those rows (sync_disk=False skips the real extractors/ directory scan).
"""
import json
import sqlite3

import pytest

from core import manager, router


@pytest.fixture
def settings_db(tmp_path, monkeypatch):
    """An isolated settings.db with ext_registry table."""
    db_path = str(tmp_path / "settings.db")
    monkeypatch.setattr('core.manager.get_settings_db_path', lambda: db_path)
    monkeypatch.setattr('core.registry.get_settings_db_path', lambda: db_path)
    manager.init_settings_db()
    return db_path


def _register_kernel(db_path, kernel_id, module_name, extensions, target_type='file'):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO ext_registry
               (kernel_id, module_name, version, file_hash, extensions, status,
                kernel_type, target_type, description, is_enabled, certified_at)
               VALUES (?, ?, '1.0.0', 'abc123', ?, 'certified', 'python', ?, '', 1, '2026-01-01')""",
            (kernel_id, module_name, json.dumps(extensions), target_type)
        )
        conn.commit()


def test_pdf_routes_to_text_and_image(settings_db):
    _register_kernel(settings_db, 'com.docvault.system.text', 'text_extractor', ['pdf'])
    _register_kernel(settings_db, 'com.docvault.system.image', 'image_extractor', ['pdf'])
    router.reload(sync_disk=False)

    names = [e.__name__ for e in router.get_extractors('pdf')]
    assert 'text_extractor' in names
    assert 'image_extractor' in names


def test_docx_routes_to_word(settings_db):
    _register_kernel(settings_db, 'com.docvault.system.word', 'microsoft_word_extractor', ['docx'])
    router.reload(sync_disk=False)

    names = [e.__name__ for e in router.get_extractors('docx')]
    assert 'microsoft_word_extractor' in names


def test_txt_routes_to_plaintext(settings_db):
    _register_kernel(settings_db, 'com.docvault.system.plaintext', 'plaintext_extractor', ['txt'])
    router.reload(sync_disk=False)

    names = [e.__name__ for e in router.get_extractors('txt')]
    assert 'plaintext_extractor' in names


def test_wav_routes_to_metadata_and_aural_ai(settings_db):
    _register_kernel(settings_db, 'com.docvault.system.media_meta', 'media_technical_diagnostics_extractor', ['wav'])
    _register_kernel(settings_db, 'com.docvault.system.aural', 'aural_intelligence_extractor', ['wav'])
    router.reload(sync_disk=False)

    names = [e.__name__ for e in router.get_extractors('wav')]
    assert 'media_technical_diagnostics_extractor' in names
    assert 'aural_intelligence_extractor' in names


def test_mp4_routes_to_multimodal_video(settings_db):
    _register_kernel(settings_db, 'com.docvault.video.multimodal', 'multimodal_video_intelligence_extractor', ['mp4'])
    router.reload(sync_disk=False)

    names = [e.__name__ for e in router.get_extractors('mp4')]
    assert 'multimodal_video_intelligence_extractor' in names


def test_jpg_routes_to_intelligent_image(settings_db):
    _register_kernel(settings_db, 'com.docvault.system.image_intel', 'intelligent_image_extractor', ['jpg'])
    router.reload(sync_disk=False)

    names = [e.__name__ for e in router.get_extractors('jpg')]
    assert 'intelligent_image_extractor' in names


def test_unknown_extension_with_no_fallback_returns_empty(settings_db):
    _register_kernel(settings_db, 'com.docvault.system.text', 'text_extractor', ['pdf'])
    router.reload(sync_disk=False)

    assert router.get_extractors('xyz123') == []


def test_unknown_extension_with_fallback_kernel_registered(settings_db):
    _register_kernel(settings_db, 'com.docvault.system.fallback', 'fallback_kernel', ['*'])
    router.reload(sync_disk=False)

    extractors = router.get_extractors('xyz123')
    assert len(extractors) == 1
    assert extractors[0].__name__ == 'fallback_kernel'


def test_reload_clears_stale_fallback_kernel(settings_db):
    """A fallback kernel deactivated between reloads must stop being routed to."""
    _register_kernel(settings_db, 'com.docvault.system.fallback', 'fallback_kernel', ['*'])
    router.reload(sync_disk=False)
    assert router.get_extractors('xyz123')[0].__name__ == 'fallback_kernel'

    with sqlite3.connect(settings_db) as conn:
        conn.execute(
            "UPDATE ext_registry SET is_enabled = 0 WHERE kernel_id = 'com.docvault.system.fallback'"
        )
        conn.commit()
    router.reload(sync_disk=False)

    assert router.get_extractors('xyz123') == []
