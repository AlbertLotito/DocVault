"""Built-in reader for pre-97 Word files that Word's File Block refuses.

81 library .doc files failed with "You are attempting to open a file type that
is blocked by your File Block settings": 49 Word 6.0/95 (OLE2, wIdent 0xA5DC)
and 32 Word for Windows 1.x/2.0 (raw, wIdent 0xA5DB / 0xA59B). In all of them
the document text is one 8-bit block between fcMin and fcMac in the header.
"""
import struct
import threading
from unittest.mock import MagicMock, patch

import pytest

from core import legacy_doc


def _word2_file(text: bytes, wident=0xA5DB, nfib=45, header=0x180):
    buf = bytearray(header)
    struct.pack_into('<HH', buf, 0, wident, nfib)
    struct.pack_into('<II', buf, 0x18, header, header + len(text))
    return bytes(buf) + text + b'\x00' * 64


def test_word2_text_between_fcmin_and_fcmac():
    raw = _word2_file(b'Dear Ms. Poole,\rThank you for the referral.\r')
    assert legacy_doc.fc_range_text(raw) == 'Dear Ms. Poole,\nThank you for the referral.'


def test_special_characters_are_cleaned():
    body = (b'Name\x07Age\x07\x07'          # table cells / row end
            b'line one\x0bline two\r'        # soft line break
            b'\x13 HYPERLINK "http://x" \x14Click here\x15 done\r'   # field: keep result only
            b'co\x1eoperate re\x1fformat\r'  # non-breaking / optional hyphen
            b'Page\x0cTwo caf\xe9')          # page break, cp1252
    text = legacy_doc.fc_range_text(_word2_file(body))
    assert 'Name\tAge' in text
    assert 'line one\nline two' in text
    assert 'Click here done' in text and 'HYPERLINK' not in text
    assert 'co-operate reformat' in text
    assert 'Page\nTwo café' in text


def test_word1_signature_also_supported(tmp_path):
    p = tmp_path / 'old.doc'
    p.write_bytes(_word2_file(b'Word 1.1 letter\r', wident=0xA59B, nfib=33))
    assert legacy_doc.text_from_legacy_doc(str(p)) == 'Word 1.1 letter'


def test_bad_offsets_are_rejected():
    buf = bytearray(_word2_file(b'x'))
    struct.pack_into('<II', buf, 0x18, 10_000, 9_000)
    with pytest.raises(ValueError):
        legacy_doc.fc_range_text(bytes(buf))


def test_unknown_format_is_rejected(tmp_path):
    p = tmp_path / 'x.doc'; p.write_bytes(b'{\\rtf1 not a word binary}')
    with pytest.raises(ValueError):
        legacy_doc.text_from_legacy_doc(str(p))


def test_word6_ole_uses_the_worddocument_stream(tmp_path, monkeypatch):
    stream = _word2_file(b'Plot outline, chapter one\r', wident=0xA5DC, nfib=104)
    fake = MagicMock(); fake.openstream.return_value.read.return_value = stream
    monkeypatch.setattr(legacy_doc, '_open_ole', lambda path: fake)
    p = tmp_path / 'w6.doc'; p.write_bytes(b'\xd0\xcf\x11\xe0' + b'\x00' * 60)
    assert legacy_doc.text_from_legacy_doc(str(p)) == 'Plot outline, chapter one'


def test_word97_is_left_to_word(tmp_path, monkeypatch):
    stream = bytearray(_word2_file(b'x')); struct.pack_into('<H', stream, 0, 0xA5EC)
    fake = MagicMock(); fake.openstream.return_value.read.return_value = bytes(stream)
    monkeypatch.setattr(legacy_doc, '_open_ole', lambda path: fake)
    p = tmp_path / 'w97.doc'; p.write_bytes(b'\xd0\xcf\x11\xe0' + b'\x00' * 60)
    with pytest.raises(ValueError, match='Word 97'):
        legacy_doc.text_from_legacy_doc(str(p))


# ── Word kernel wiring: Word first (unchanged), built-in reader if Word refuses ──

def _ctx():
    from core.extractors.base import ExtractorContext, ExtractorLogger
    return ExtractorContext(vault_id='t', file_hash='t', cancel_token=threading.Event(),
                            logger=MagicMock(spec=ExtractorLogger), settings=MagicMock())


def test_word_kernel_falls_back_when_word_refuses(tmp_path):
    from extractors import microsoft_word_extractor as wx
    p = tmp_path / 'letter.doc'; p.write_bytes(_word2_file(b'Old letter text\r'))
    blocked = (None, 'Legacy .doc extraction failed (Word may not be installed): blocked by your File Block settings')
    with patch.object(wx, '_extract_doc_legacy', return_value=blocked):
        text, error, meta = wx.extract(str(p), _ctx())
    assert (text, error) == ('Old letter text', None)
    assert meta['reader'] == 'builtin' and 'File Block' in meta['word_error']


def test_word_kernel_still_uses_word_first(tmp_path):
    from extractors import microsoft_word_extractor as wx
    p = tmp_path / 'letter.doc'; p.write_bytes(_word2_file(b'ignored\r'))
    with patch.object(wx, '_extract_doc_legacy', return_value=('Text via Word', None)) as com:
        result = wx.extract(str(p), _ctx())
    com.assert_called_once()
    assert result[:2] == ('Text via Word', None)


def test_word_kernel_reports_word_error_when_builtin_cannot_read_either(tmp_path):
    from extractors import microsoft_word_extractor as wx
    p = tmp_path / 'weird.doc'; p.write_bytes(b'<html>not word</html>')
    with patch.object(wx, '_extract_doc_legacy', return_value=(None, 'Legacy .doc extraction failed: boom')):
        result = wx.extract(str(p), _ctx())
    assert result[0] is None and 'boom' in result[1]
