# KML/KMZ Extractor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a KML/KMZ extraction kernel that indexes placemark names and descriptions for full-text search.

**Architecture:** Single new kernel `extractors/kml_extractor.py` following the standard DocVault pattern (MANIFEST + `extract(file_path, ctx) → (text, err, meta)`). KMZ files are unzipped in-memory via `zipfile` to extract the embedded KML. No new dependencies — stdlib only.

**Tech Stack:** Python stdlib `xml.etree.ElementTree`, `zipfile`, `re`

---

### Task 1: Write Failing Tests

**Files:**
- Create: `tests/test_kml_extractor.py`

**Step 1: Write the test file**

```python
"""
tests/test_kml_extractor.py
Unit tests for the KML/KMZ extractor kernel.
Run: pytest tests/test_kml_extractor.py -v
"""
import io
import os
import zipfile
import tempfile
import importlib.util

# --- Load module under test ---
_ROOT = os.path.dirname(os.path.dirname(__file__))
_MOD_PATH = os.path.join(_ROOT, 'extractors', 'kml_extractor.py')

def _load_mod():
    spec = importlib.util.spec_from_file_location("kml_extractor", _MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

mod = _load_mod()

# --- KML fixtures ---

KML_SIMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Test Map</name>
    <Placemark>
      <name>Eiffel Tower</name>
      <description>Iconic iron lattice tower in Paris.</description>
    </Placemark>
    <Placemark>
      <name>Big Ben</name>
    </Placemark>
  </Document>
</kml>"""

KML_LEGACY_NS = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://earth.google.com/kml/2.1">
  <Document>
    <Placemark>
      <name>Legacy Place</name>
      <description>Old namespace works fine.</description>
    </Placemark>
  </Document>
</kml>"""

KML_HTML_DESC = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>Place with HTML</name>
      <description><![CDATA[<b>Bold text</b> and <br/> a line break]]></description>
    </Placemark>
  </Document>
</kml>"""

KML_EMPTY = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Empty Map</name>
  </Document>
</kml>"""

KML_SKIP_UNNAMED = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <description>No name — should be skipped</description>
    </Placemark>
    <Placemark>
      <name>Named Place</name>
      <description>This one has a name.</description>
    </Placemark>
  </Document>
</kml>"""

KML_MALFORMED = b"<kml><this is not valid xml"


# --- Helpers ---

def make_ctx():
    from core.extractors.base import ExtractorContext, ExtractorLogger
    import threading
    return ExtractorContext(
        vault_id="test-vault",
        file_hash="abc123",
        logger=ExtractorLogger("test"),
        cancel_token=threading.Event(),
    )

def write_temp(content: bytes, suffix: str) -> str:
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    f.write(content)
    f.close()
    return f.name

def make_kmz(kml_bytes: bytes, kml_name: str = 'doc.kml') -> str:
    f = tempfile.NamedTemporaryFile(suffix='.kmz', delete=False)
    f.close()
    with zipfile.ZipFile(f.name, 'w') as zf:
        zf.writestr(kml_name, kml_bytes)
    return f.name


# --- Tests ---

def test_extract_kml_basic():
    path = write_temp(KML_SIMPLE, '.kml')
    try:
        result, err, meta = mod.extract(path, make_ctx())
        assert err is None
        assert isinstance(result, str)
        assert 'Eiffel Tower' in result
        assert 'Iconic iron lattice tower in Paris.' in result
        assert 'Big Ben' in result
    finally:
        os.unlink(path)

def test_extract_kml_no_description_shows_placeholder():
    path = write_temp(KML_SIMPLE, '.kml')
    try:
        result, err, meta = mod.extract(path, make_ctx())
        assert err is None
        assert '(no description)' in result
    finally:
        os.unlink(path)

def test_extract_kml_html_stripped():
    path = write_temp(KML_HTML_DESC, '.kml')
    try:
        result, err, meta = mod.extract(path, make_ctx())
        assert err is None
        assert 'Bold text' in result
        assert '<b>' not in result
        assert '<![CDATA[' not in result
        assert '<br' not in result
    finally:
        os.unlink(path)

def test_extract_kml_legacy_namespace():
    path = write_temp(KML_LEGACY_NS, '.kml')
    try:
        result, err, meta = mod.extract(path, make_ctx())
        assert err is None
        assert 'Legacy Place' in result
        assert 'Old namespace works fine.' in result
    finally:
        os.unlink(path)

def test_extract_kml_empty_returns_valid_result():
    path = write_temp(KML_EMPTY, '.kml')
    try:
        result, err, meta = mod.extract(path, make_ctx())
        assert err is None
        assert isinstance(result, str)
        assert 'Placemarks: 0' in result
    finally:
        os.unlink(path)

def test_extract_kml_skips_unnamed_placemarks():
    path = write_temp(KML_SKIP_UNNAMED, '.kml')
    try:
        result, err, meta = mod.extract(path, make_ctx())
        assert err is None
        assert 'Named Place' in result
        assert 'Placemarks: 1' in result
    finally:
        os.unlink(path)

def test_extract_kml_document_name_in_header():
    path = write_temp(KML_SIMPLE, '.kml')
    try:
        result, err, meta = mod.extract(path, make_ctx())
        assert err is None
        assert 'Test Map' in result
    finally:
        os.unlink(path)

def test_extract_kml_placemark_count_in_meta():
    path = write_temp(KML_SIMPLE, '.kml')
    try:
        result, err, meta = mod.extract(path, make_ctx())
        assert err is None
        assert meta['placemark_count'] == 2
        assert meta['format'] == 'KML'
    finally:
        os.unlink(path)

def test_extract_kmz_basic():
    path = make_kmz(KML_SIMPLE)
    try:
        result, err, meta = mod.extract(path, make_ctx())
        assert err is None
        assert 'Eiffel Tower' in result
        assert meta['format'] == 'KMZ'
    finally:
        os.unlink(path)

def test_extract_kmz_no_kml_entry():
    f = tempfile.NamedTemporaryFile(suffix='.kmz', delete=False)
    f.close()
    with zipfile.ZipFile(f.name, 'w') as zf:
        zf.writestr('readme.txt', 'no kml here')
    try:
        result, err, meta = mod.extract(f.name, make_ctx())
        assert result is None
        assert err is not None
        assert 'kml' in err.lower()
    finally:
        os.unlink(f.name)

def test_extract_malformed_xml():
    path = write_temp(KML_MALFORMED, '.kml')
    try:
        result, err, meta = mod.extract(path, make_ctx())
        assert result is None
        assert err is not None
    finally:
        os.unlink(path)

def test_extract_missing_file():
    result, err, meta = mod.extract('/nonexistent/file.kml', make_ctx())
    assert result is None
    assert err is not None

def test_extract_returns_3tuple():
    path = write_temp(KML_SIMPLE, '.kml')
    try:
        ret = mod.extract(path, make_ctx())
        assert len(ret) == 3
    finally:
        os.unlink(path)
```

**Step 2: Run tests — verify they all fail**

```bash
cd E:/DocVault && venv/Scripts/python -m pytest tests/test_kml_extractor.py -v 2>&1 | head -20
```

Expected: `FileNotFoundError` or `AttributeError: 'NoneType' object has no attribute 'loader'` — NOT passing.

**Step 3: Commit the test file**

```bash
git add tests/test_kml_extractor.py
git commit -m "test: add failing tests for KML/KMZ extractor kernel"
```

---

### Task 2: Write the Kernel

**Files:**
- Create: `extractors/kml_extractor.py`

**Step 1: Write the kernel**

```python
"""
KML/KMZ Extractor Kernel — extracts placemark names and descriptions.
Supports KML (plain XML) and KMZ (ZIP-wrapped KML).
No external dependencies — stdlib only.
"""

MANIFEST = {
    "id": "com.docvault.system.kml",
    "version": "1.0.0",
    "name": "KML/KMZ Extractor",
    "extensions": ["kml", "kmz"],
    "requires": [],
}

__description__ = (
    "Extracts placemark names and descriptions from KML and KMZ geographic files. "
    "KMZ archives are unzipped in-memory. HTML tags and CDATA wrappers in "
    "descriptions are stripped before indexing."
)

import os
import re
import zipfile
import xml.etree.ElementTree as ET
from core.extractors.base import ExtractorContext

# KML uses one of these two namespaces
_KNOWN_NS = {
    'http://www.opengis.net/kml/2.2',
    'http://earth.google.com/kml/2.1',
}


def _detect_ns(root) -> str:
    """Extract XML namespace from root element tag, e.g. '{http://...}kml' → 'http://...'"""
    m = re.match(r'\{([^}]+)\}', root.tag)
    return m.group(1) if m else ''


def _strip_html(text: str) -> str:
    """Decode CDATA blocks and strip HTML tags from a description string."""
    # Unwrap CDATA sections
    text = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', text, flags=re.DOTALL)
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _read_kml_bytes(file_path: str) -> bytes:
    """Read raw KML bytes from a .kml or .kmz file."""
    if file_path.lower().endswith('.kmz'):
        with zipfile.ZipFile(file_path, 'r') as zf:
            kml_names = [n for n in zf.namelist() if n.lower().endswith('.kml')]
            if not kml_names:
                raise ValueError("No KML file found in KMZ")
            # Prefer doc.kml; otherwise take first .kml entry
            name = next(
                (n for n in kml_names if os.path.basename(n).lower() == 'doc.kml'),
                kml_names[0]
            )
            return zf.read(name)
    else:
        with open(file_path, 'rb') as f:
            return f.read()


def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    archive_fmt = 'KMZ' if file_path.lower().endswith('.kmz') else 'KML'

    # --- Read bytes ---
    try:
        xml_bytes = _read_kml_bytes(file_path)
    except Exception as e:
        return None, f"KML read failed: {e}", None

    # --- Parse XML ---
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        return None, f"KML parse error: {e}", None

    ns = _detect_ns(root)
    p = f'{{{ns}}}' if ns else ''

    # --- Extract document name ---
    doc_name = ''
    for candidate in [
        root.find(f'.//{p}Document/{p}name'),
        root.find(f'{p}name'),
    ]:
        if candidate is not None and candidate.text:
            doc_name = candidate.text.strip()
            break
    if not doc_name:
        doc_name = os.path.basename(file_path)

    # --- Extract placemarks ---
    placemarks = []
    for pm in root.findall(f'.//{p}Placemark'):
        name_el = pm.find(f'{p}name')
        name = name_el.text.strip() if name_el is not None and name_el.text else ''
        if not name:
            continue  # skip unnamed placemarks

        desc_el = pm.find(f'{p}description')
        if desc_el is not None and desc_el.text:
            desc = _strip_html(desc_el.text)
        else:
            desc = ''

        placemarks.append((name, desc))

    # --- Build output ---
    lines = [
        f"KML Document: {doc_name}",
        f"Placemarks: {len(placemarks)}",
        "",
    ]

    if not placemarks:
        lines.append("(no placemarks)")
    else:
        for name, desc in placemarks:
            lines.append(name)
            lines.append(f"  {desc}" if desc else "  (no description)")
            lines.append("")

    meta = {
        'format': archive_fmt,
        'placemark_count': len(placemarks),
        'document_name': doc_name,
    }

    return '\n'.join(lines), None, meta
```

**Step 2: Run the full test suite — all must pass**

```bash
cd E:/DocVault && venv/Scripts/python -m pytest tests/test_kml_extractor.py -v
```

Expected: **13 passed**. If any fail, debug the kernel (not the tests).

**Step 3: Commit**

```bash
git add extractors/kml_extractor.py
git commit -m "feat: implement KML/KMZ extractor kernel"
```

---

### Task 3: Verify Registration

**Files:** No changes — the registry auto-discovers the kernel on startup.

**Step 1: Start the server**

```bash
cd E:/DocVault && venv/Scripts/python run.py
```

Watch startup logs for:
```
[registry] Discovered kernel: com.docvault.system.kml
[registry] Auto-certified system kernel: com.docvault.system.kml
```

**Step 2: Verify in Extractor Lab**

Open `http://localhost:8000/lab`

- **KML/KMZ Extractor** should appear in the sidebar with a `certified` pill
- Click it → Certification panel should show all checks passing

**Step 3: Test with a real KML file via the Lab**

1. Click KML/KMZ Extractor in sidebar
2. Click Test panel
3. Browse to any `.kml` file on disk (Google Earth exports, GIS exports, etc.)
4. Click Run
5. Verify output shows `KML Document:`, `Placemarks:`, and the listing

**Step 4: Update memory**

Add to `C:\Users\Albert\.claude\projects\E--DocVault\memory\MEMORY.md`:
```
- `extractors/kml_extractor.py` — KML/KMZ kernel; extracts placemark names+descriptions; no deps (stdlib only)
```

---

### Notes

- **No new pip dependencies** — this kernel is pure stdlib.
- **KMZ preference:** if a KMZ contains multiple `.kml` files, `doc.kml` is preferred; otherwise the first `.kml` entry is used. This matches Google Earth's convention.
- **Retry old errors:** if `.kml`/`.kmz` files previously hit the fallback kernel (ERROR status), use Utilities → Health Check → "Retry extraction errors" after the server restarts with the new kernel.
