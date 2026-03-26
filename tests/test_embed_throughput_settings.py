from core.settings import SETTINGS_SCHEMA


def test_embed_batch_size_setting_exists():
    assert 'workers:embed_batch_size' in SETTINGS_SCHEMA
    s = SETTINGS_SCHEMA['workers:embed_batch_size']
    assert s['type'] == 'int'
    assert s['default'] == 8
    assert 'label' in s and len(s['label']) > 0
    assert s['group'] == 'embeddings'
    assert 'description' in s and len(s['description']) > 0


def test_embed_concurrency_setting_exists():
    assert 'workers:embed_concurrency' in SETTINGS_SCHEMA
    s = SETTINGS_SCHEMA['workers:embed_concurrency']
    assert s['type'] == 'int'
    assert s['default'] == 1
    assert 'label' in s and len(s['label']) > 0
    assert s['group'] == 'embeddings'
    assert 'description' in s and len(s['description']) > 0
