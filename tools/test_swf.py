"""
SWF extraction test rig.
Tests pyswf and pure-Python header parsing against a sample file.
Usage: python tools/test_swf.py "path/to/file.swf"
"""
import sys
import struct
import zlib
import os

SWF_FILE = sys.argv[1] if len(sys.argv) > 1 else r"Z:\Documents\A Pocono Country Place\Newspaper\2017\APCP JAN 2017.swf"


# ── 1. Pure-Python header parse ───────────────────────────────────────────────

def parse_header(path):
    with open(path, 'rb') as f:
        raw = f.read()

    sig = raw[:3].decode('ascii', errors='replace')
    version = raw[3]
    file_len = struct.unpack_from('<I', raw, 4)[0]

    compression = {
        'FWS': 'none',
        'CWS': 'zlib',
        'ZWS': 'lzma',
    }.get(sig, 'unknown')

    print(f"=== HEADER ===")
    print(f"  Signature  : {sig}")
    print(f"  Compression: {compression}")
    print(f"  SWF version: {version}")
    print(f"  Stored size: {file_len:,} bytes  (actual: {len(raw):,})")

    # Decompress body so we can read frame info
    try:
        if compression == 'zlib':
            body = zlib.decompress(raw[8:])
        elif compression == 'lzma':
            import lzma
            # ZWS: 4-byte uncompressed size after the 8-byte standard header
            body = lzma.decompress(raw[12:])
        else:
            body = raw[8:]
    except Exception as e:
        print(f"  [!] Decompression failed: {e}")
        return

    # RECT structure: first 5 bits = Nbits, then 4 fields of Nbits each
    nbits = (body[0] >> 3) & 0x1F
    total_bits = 5 + nbits * 4
    rect_bytes = (total_bits + 7) // 8

    # Frame rate and count follow the RECT
    frame_rate_raw = struct.unpack_from('<H', body, rect_bytes)[0]
    frame_count    = struct.unpack_from('<H', body, rect_bytes + 2)[0]
    frame_rate     = frame_rate_raw / 256.0

    print(f"  Frame rate : {frame_rate:.2f} fps")
    print(f"  Frame count: {frame_count}")
    print()
    return body, rect_bytes + 4   # offset where tags begin


# ── 2. pyswf parse ────────────────────────────────────────────────────────────

def parse_pyswf(path):
    print("=== PYSWF ===")
    try:
        from swf.movie import SWF
        from swf.tag import TagDefineFont, TagDefineFontInfo, TagDoABC, TagPlaceObject

        with open(path, 'rb') as f:
            swf = SWF(f)

        print(f"  Version    : {swf.header.version}")
        print(f"  Frame size : {swf.header.frame_size}")
        print(f"  Frame rate : {swf.header.frame_rate}")
        print(f"  Frame count: {swf.header.frame_count}")
        print(f"  Tag count  : {len(swf.tags)}")

        # Collect tag type names
        from collections import Counter
        tag_types = Counter(type(t).__name__ for t in swf.tags)
        print(f"\n  Tag type breakdown (top 15):")
        for name, count in tag_types.most_common(15):
            print(f"    {count:4d}  {name}")

        # Extract any embedded text
        texts = []
        for tag in swf.tags:
            # DefineEditText / StaticText carry human-readable strings
            name = type(tag).__name__
            if 'Text' in name or 'Edit' in name:
                for attr in ('html_text', 'initial_text', 'text', 'records'):
                    val = getattr(tag, attr, None)
                    if isinstance(val, str) and val.strip():
                        texts.append(val.strip())
                    elif isinstance(val, (list, tuple)):
                        for rec in val:
                            t2 = getattr(rec, 'text', None)
                            if isinstance(t2, str) and t2.strip():
                                texts.append(t2.strip())

        if texts:
            print(f"\n  Embedded text strings ({len(texts)} found):")
            for t in texts[:30]:
                print(f"    {repr(t)}")
        else:
            print("\n  No embedded text strings found via tag scan.")

        # Check for ActionScript (ABC tags = AS3)
        abc_tags = [t for t in swf.tags if type(t).__name__ in ('TagDoABC', 'DoABC')]
        if abc_tags:
            print(f"\n  ActionScript 3 (ABC) blocks: {len(abc_tags)}")

    except Exception as e:
        import traceback
        print(f"  [!] pyswf failed: {e}")
        traceback.print_exc()
    print()


# ── 3. String scan (fallback) ─────────────────────────────────────────────────

def string_scan(path, min_len=6):
    print("=== RAW STRING SCAN (printable runs) ===")
    with open(path, 'rb') as f:
        raw = f.read()

    # Decompress if needed
    sig = raw[:3].decode('ascii', errors='replace')
    try:
        if sig == 'CWS':
            raw = raw[:8] + zlib.decompress(raw[8:])
        elif sig == 'ZWS':
            import lzma
            raw = raw[:8] + lzma.decompress(raw[12:])
    except Exception:
        pass

    strings = []
    current = []
    for b in raw:
        if 32 <= b < 127:
            current.append(chr(b))
        else:
            if len(current) >= min_len:
                strings.append(''.join(current))
            current = []
    if len(current) >= min_len:
        strings.append(''.join(current))

    # Deduplicate while preserving order, skip boilerplate
    seen = set()
    unique = []
    for s in strings:
        if s not in seen:
            seen.add(s)
            unique.append(s)

    print(f"  {len(unique)} unique strings of length >= {min_len}")
    print(f"  Sample (first 40):")
    for s in unique[:40]:
        print(f"    {repr(s)}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

if not os.path.exists(SWF_FILE):
    print(f"File not found: {SWF_FILE}")
    sys.exit(1)

print(f"Target: {SWF_FILE}\n")
parse_header(SWF_FILE)
parse_pyswf(SWF_FILE)
string_scan(SWF_FILE)
