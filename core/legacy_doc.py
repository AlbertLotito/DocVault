"""Built-in text reader for pre-97 Word documents.

Word's Trust Center "File Block" refuses Word 6.0/95 and Word for Windows
1.x/2.0 files by default, so COM automation fails on them. Their text is stored
as one 8-bit (cp1252) block between fcMin and fcMac in the file header:

  Word 6.0/95          OLE2 container, 'WordDocument' stream, wIdent 0xA5DC
  Word for Windows 2.0 raw file, wIdent 0xA5DB
  Word for Windows 1.x raw file, wIdent 0xA59B

"Fast-saved" (complex) Word 6 files can also hold stale/deleted text in that
range; that is acceptable for indexing. Word 97+ (wIdent 0xA5EC) stores text in
pieces and is left to Word.
"""
import re
import struct

_OLE_MAGIC = b'\xd0\xcf\x11\xe0'
_FC_RANGE_FORMATS = {0xA5DC: 'Word 6.0/95', 0xA5DB: 'Word 2.0', 0xA59B: 'Word 1.x'}
_WORD97 = 0xA5EC

# Field codes: \x13 instruction \x14 result \x15  ->  keep the result only.
_FIELD = re.compile('\x13[^\x13\x14\x15]*(?:\x14([^\x13\x15]*))?\x15')
_TRANSLATE = str.maketrans({
    '\r': '\n', '\x0b': '\n', '\x0c': '\n',    # paragraph, line break, page break
    '\x07': '\t',                              # table cell / row mark
    '\x1e': '-', '\x1f': '',                   # non-breaking / optional hyphen
    '\x01': '', '\x08': '', '\x05': '',        # embedded object / drawing / annotation anchors
})
_LEFTOVER_CONTROL = re.compile(r'[\x00-\x08\x0e-\x1f]')


def fc_range_text(buf: bytes) -> str:
    """Text between fcMin and fcMac (header offsets 0x18 / 0x1C), cleaned."""
    fc_min, fc_mac = struct.unpack_from('<II', buf, 0x18)
    if not (0 < fc_min < fc_mac <= len(buf)):
        raise ValueError(f'implausible text range {fc_min}..{fc_mac} (size {len(buf)})')
    text = buf[fc_min:fc_mac].decode('cp1252', errors='replace')
    for _ in range(3):                          # nested fields resolve inside-out
        text = _FIELD.sub(lambda m: m.group(1) or '', text)
    text = _LEFTOVER_CONTROL.sub('', text.translate(_TRANSLATE))
    text = re.sub(r'\t\t+', '\t', text)         # cell mark + row mark
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _open_ole(path: str):
    import olefile
    return olefile.OleFileIO(path)


def text_from_legacy_doc(path: str) -> str:
    """Text of a pre-97 Word file; ValueError if it isn't one this reader handles."""
    with open(path, 'rb') as f:
        head = f.read(4)
    if head.startswith(_OLE_MAGIC):
        ole = _open_ole(path)
        try:
            buf = ole.openstream('WordDocument').read()
        finally:
            ole.close()
    else:
        with open(path, 'rb') as f:
            buf = f.read()
    if len(buf) < 0x20:
        raise ValueError('file too short to be a Word document')
    wident = struct.unpack_from('<H', buf, 0)[0]
    if wident == _WORD97:
        raise ValueError('Word 97+ binary format: not handled by the built-in reader')
    if wident not in _FC_RANGE_FORMATS:
        raise ValueError(f'not a pre-97 Word document (wIdent {wident:#06x})')
    return fc_range_text(buf)
