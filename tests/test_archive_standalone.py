"""
Standalone .gz/.bz2 files (one compressed file, not a tar archive) — e.g. the
56 rotated web-server access logs in the library — failed with "Standalone
compressed file — not a tar archive". They are decompressed and indexed instead.
"""
import bz2
import gzip
import io
import tarfile
import threading
from unittest.mock import MagicMock

from extractors import archive_xray_extractor as ax


def _ctx():
    from core.extractors.base import ExtractorContext, ExtractorLogger
    return ExtractorContext(vault_id='t', file_hash='t', cancel_token=threading.Event(),
                            logger=MagicMock(spec=ExtractorLogger), settings=MagicMock())


LOG = (b'203.0.113.9 - - [17/Sep/2011:10:01:02 -0400] "GET /index.html HTTP/1.1" 200 5120\n'
       b'198.51.100.4 - - [17/Sep/2011:10:01:09 -0400] "GET /contact.php HTTP/1.1" 404 312\n')


def test_gzip_text_file_is_decompressed_and_indexed(tmp_path):
    f = tmp_path / 'access_log_20110917.gz'
    f.write_bytes(gzip.compress(LOG))

    text, error, meta = ax.extract(str(f), _ctx())

    assert error is None
    assert 'GET /contact.php' in text
    assert 'access_log_20110917' in text            # inner file named in the header
    assert meta['format'] == 'GZIP'
    assert meta['inner_name'] == 'access_log_20110917'
    assert meta['uncompressed_size'] == len(LOG)
    assert not meta.get('truncated')


def test_bzip2_text_file(tmp_path):
    f = tmp_path / 'notes.txt.bz2'
    f.write_bytes(bz2.compress(b'Meeting notes: renew the lease in March.\n'))
    text, error, meta = ax.extract(str(f), _ctx())
    assert error is None
    assert 'renew the lease' in text
    assert meta['format'] == 'BZIP2' and meta['inner_name'] == 'notes.txt'


def test_binary_payload_is_described_not_an_error(tmp_path):
    f = tmp_path / 'firmware.bin.gz'
    f.write_bytes(gzip.compress(bytes(range(256)) * 64))
    text, error, meta = ax.extract(str(f), _ctx())
    assert error is None
    assert 'firmware.bin' in text and 'binary' in text.lower()
    assert '\x00' not in text


def test_huge_text_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(ax, '_MAX_STANDALONE_TEXT', 1000)
    f = tmp_path / 'big.log.gz'
    f.write_bytes(gzip.compress(LOG * 200))
    text, error, meta = ax.extract(str(f), _ctx())
    assert error is None
    assert meta['truncated'] is True
    assert len(text) < 1500


def test_real_tar_gz_still_lists_entries(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        data = b'hello'
        info = tarfile.TarInfo('docs/readme.txt'); info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    f = tmp_path / 'bundle.tar.gz'
    f.write_bytes(buf.getvalue())
    text, error, meta = ax.extract(str(f), _ctx())
    assert error is None
    assert 'readme.txt' in text and meta['file_count'] == 1


def test_corrupt_gzip_is_a_readable_error(tmp_path):
    f = tmp_path / 'broken.gz'
    f.write_bytes(b'\x1f\x8b\x08\x00' + b'\x00' * 20)
    text, error, _ = ax.extract(str(f), _ctx())
    assert text is None
    assert error
