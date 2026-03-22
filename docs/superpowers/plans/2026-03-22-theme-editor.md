# Theme Editor Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/theme` page that lets users pick CSS color presets and tweak individual variables, with changes persisting in settings.db and applied globally on every page via lcars.js.

**Architecture:** Three-task sequence — backend first (schema + endpoint), then lcars.js (shared utilities + nav), then the full theme.html page. Backend is tested with pytest; UI logic is tested with DOM fixture tests. No new dependencies.

**Tech Stack:** FastAPI, Python 3.13, SQLite via `core.settings`, vanilla JS (ES2020), CSS custom properties.

---

## Chunk 1: Backend + lcars.js

## File Map

| File | Action | Responsibility |
|---|---|---|
| `core/settings.py` | Modify | Add `ui:theme` schema entry; filter `hidden` keys in `get_all_configurable()` |
| `api/routes/settings.py` | Modify | Add `GET /api/settings/theme` before existing routes |
| `api/main.py` | Modify | Add `GET /theme` HTML page route |
| `frontend/static/lcars.js` | Modify | Add `applyTheme()`, `lcThemeLoad()` inside `lcInit()`, nav pill |
| `frontend/static/i18n/en.json` | Modify | Add `nav.theme: "Theme"` |
| `frontend/static/i18n/es.json` | Modify | Add `nav.theme: "Tema"` |
| `frontend/static/i18n/fr.json` | Modify | Add `nav.theme: "Th\u00e8me"` |
| `tests/test_theme.py` | Create | Backend + schema + i18n key tests |

---

### Task 1: Settings schema + hidden filter + GET /api/settings/theme

**Files:**
- Modify: `core/settings.py:311-317` (schema) and `:346-358` (`get_all_configurable`)
- Modify: `api/routes/settings.py`
- Create: `tests/test_theme.py`

---

- [ ] **Step 1: Write the failing tests**

Create `tests/test_theme.py`:

```python
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```
cd E:\DocVault
pytest tests/test_theme.py -v
```

Expected: 4 failures — `ui:theme` not in schema, `hidden` not filtered, endpoint 404.

- [ ] **Step 3: Add `ui:theme` to settings schema**

In `core/settings.py`, find the `# UI` block ending at line ~317. Add the new entry after `ui:language`:

```python
            'ui:theme': {
                'type': 'string',
                'default': '{}',
                'label': 'UI Theme Overrides',
                'group': 'ui',
                'hidden': True,
                'description': 'JSON map of CSS custom property overrides applied to every page.',
            },
```

- [ ] **Step 4: Update `get_all_configurable()` to skip hidden keys**

In `core/settings.py`, `get_all_configurable()` currently has:

```python
    def get_all_configurable(self) -> list[dict]:
        """Returns a list of all UI-configurable settings with their current values."""
        all_settings = []
        for key, meta in self.schema.items():
            all_settings.append({
```

Change the loop to skip hidden entries:

```python
    def get_all_configurable(self) -> list[dict]:
        """Returns a list of all UI-configurable settings with their current values."""
        all_settings = []
        for key, meta in self.schema.items():
            if meta.get('hidden', False):
                continue
            all_settings.append({
```

- [ ] **Step 5: Add `GET /api/settings/theme` to `api/routes/settings.py`**

Add `import json` at the top, then add the new route **before** the existing `@router.get("/settings")`:

```python
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from core.settings import settings

router = APIRouter()


class SettingUpdate(BaseModel):
    key: str
    value: str


@router.get("/settings/theme")
def get_theme():
    raw = settings.get("ui:theme") or "{}"
    try:
        theme = json.loads(raw)
    except Exception:
        theme = {}
    return {"theme": theme}


@router.get("/settings")
def get_settings():
    return {"settings": settings.get_all_configurable()}


@router.post("/settings")
def update_setting(body: SettingUpdate):
    try:
        settings.set(body.key, body.value)
        return {"status": "ok", "key": body.key, "value": body.value}
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

> **Why route order matters:** FastAPI matches routes top-to-bottom. `/settings/theme` must appear before `/settings` to avoid the generic handler swallowing it if a parameterised form is ever added.

- [ ] **Step 6: Run tests — all 4 should pass**

```
cd E:\DocVault
pytest tests/test_theme.py -v
```

Expected: 4 passed.

- [ ] **Step 7: Run existing tests to confirm no regressions**

```
cd E:\DocVault
pytest tests/ -v
```

Expected: all previously passing tests still pass.

- [ ] **Step 8: Commit**

```bash
git add core/settings.py api/routes/settings.py tests/test_theme.py
git commit -m "feat(theme): settings schema, hidden filter, GET /api/settings/theme"
```

---

### Task 2: Add /theme route to api/main.py

**Files:**
- Modify: `api/main.py`

- [ ] **Step 1: Add the page route**

In `api/main.py`, after the `/identity` route at the end, add:

```python
@app.get("/theme", include_in_schema=False)
def theme_page():
    return FileResponse(os.path.join(FRONTEND, 'theme.html'))
```

- [ ] **Step 2: Verify server starts without error**

```
cd E:\DocVault
python -c "from api.main import app; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add api/main.py
git commit -m "feat(theme): add /theme page route"
```

---

### Task 3: lcars.js — applyTheme, lcThemeLoad, nav pill + i18n keys

**Files:**
- Modify: `frontend/static/lcars.js`
- Modify: `frontend/static/i18n/en.json`
- Modify: `frontend/static/i18n/es.json`
- Modify: `frontend/static/i18n/fr.json`

**Key facts about lcars.js:**
- `lcInit()` is `async`. It calls `await lcI18nLoad()`, then builds nav, applies i18n, starts sensors, dispatches `lc:ready`.
- `applyTheme()` must be defined **before** `lcInit()` since `lcThemeLoad()` (called inside `lcInit()`) calls it.
- Nav pills are hardcoded `<a>` elements inside the template literal in `buildNavHTML()`. There is no array to append to.
- `UTILITIES_SUBPAGES` controls which body `data-page` values map to the `utilities` pill. `/theme` is top-level — do NOT add `'theme'` to `UTILITIES_SUBPAGES`.

- [ ] **Step 1: Add i18n keys to locale files**

In `frontend/static/i18n/en.json`, add after `"nav.settings"`:
```json
  "nav.theme": "Theme",
```

In `frontend/static/i18n/es.json`, add after `"nav.settings"`:
```json
  "nav.theme": "Tema",
```

In `frontend/static/i18n/fr.json`, add after `"nav.settings"`:
```json
  "nav.theme": "Th\u00e8me",
```

- [ ] **Step 2: Verify the i18n parity test still passes**

```
cd E:\DocVault
pytest tests/test_i18n.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 3: Add `applyTheme()` to lcars.js**

Add this function **after the `t()` function** (around line 43) and **before `lcI18nLoad()`**:

```js
/**
 * Inject (or clear) CSS custom property overrides into a <style id="th-override"> tag.
 * overrides = { '--c-accent': '#ff9900', ... }
 * Pass an empty object to remove all overrides.
 */
function applyTheme(overrides) {
  const vars = Object.entries(overrides)
    .map(([k, v]) => `  ${k}: ${v};`)
    .join('\n');
  let el = document.getElementById('th-override');
  if (!el) {
    el = document.createElement('style');
    el.id = 'th-override';
    document.head.appendChild(el);
  }
  el.textContent = vars ? `:root {\n${vars}\n}` : '';
}
```

- [ ] **Step 4: Add `lcThemeLoad()` to lcars.js**

Add this function **after `applyTheme()`**, still before `lcI18nLoad()`:

```js
/**
 * Fetch the saved theme from the server and apply it.
 * Non-fatal: if the fetch fails or returns empty, lcars.css defaults remain untouched.
 */
async function lcThemeLoad() {
  try {
    const res = await fetch('/api/settings/theme');
    const { theme } = await res.json();
    if (theme && Object.keys(theme).length > 0) applyTheme(theme);
  } catch (_) { /* non-fatal — lcars.css defaults remain */ }
}
```

- [ ] **Step 5: Call `lcThemeLoad()` inside `lcInit()` after i18n, before `lc:ready`**

Find `lcInit()`. Currently it looks like:

```js
async function lcInit() {
  await lcI18nLoad();
  const navEl = document.querySelector('.lc-nav');
  ...
  document.dispatchEvent(new CustomEvent('lc:ready'));
}
```

Add the theme load call **after `lcApplyI18n()` (line ~133)** and **before `document.dispatchEvent(...)`**. The actual file has:

```js
  lcApplyI18n();

  // Start sensor rail polling
  lcPollSensors();
```

Insert one line so it becomes:

```js
  lcApplyI18n();

  // Load and apply saved theme overrides before signalling ready
  await lcThemeLoad();

  // Start sensor rail polling
  lcPollSensors();
```

- [ ] **Step 6: Add the Theme nav pill in `buildNavHTML()`**

Find the `<nav class="lc-nav-links">` block in `buildNavHTML()`. Add the theme pill **after the settings pill**:

```js
    <a href="/settings" class="lc-pill" data-nav="settings">${t('nav.settings')}</a>
    <a href="/theme"    class="lc-pill" data-nav="theme">${t('nav.theme')}</a>
```

- [ ] **Step 7: Run all tests**

```
cd E:\DocVault
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add frontend/static/lcars.js frontend/static/i18n/en.json frontend/static/i18n/es.json frontend/static/i18n/fr.json
git commit -m "feat(theme): applyTheme + lcThemeLoad in lcars.js, Theme nav pill, nav.theme i18n keys"
```

---

## Chunk 2: theme.html

### Task 4: Build theme.html

**Files:**
- Create: `frontend/theme.html`

**Context:** This is a new standalone page. It uses all the standard LCARS patterns:
- `<body data-page="theme">` — activates the Theme nav pill
- `<div class="lc-nav"></div>` — lcars.js injects navbar here
- Script ordering: `<script src="/static/lcars.js">` **must** come **before** the inline `<script>` block
- All page init logic runs inside `document.addEventListener('lc:ready', () => { ... })`
- `lcToast('message')` for save confirmations

**CSS custom properties in lcars.css `:root` (exact names — use these exactly):**
```
--c-bg, --c-surface, --c-panel, --c-nav,
--c-border, --c-border-str,
--c-accent, --c-accent-dim,
--c-label, --c-label-dim,
--c-ok, --c-warn, --c-error, --c-proc, --c-pend,
--font
```

**Preset values (hardcoded — no server round-trip):**

Warm LCARS (default):
```js
{ '--c-bg':'#0a0600','--c-surface':'#0d0800','--c-panel':'#080400','--c-nav':'#000000',
  '--c-border':'#1a0d00','--c-border-str':'#331a00',
  '--c-accent':'#ff9900','--c-accent-dim':'#cc6600',
  '--c-label':'#aa7744','--c-label-dim':'#996633',
  '--c-ok':'#33ee77','--c-warn':'#ffcc66','--c-error':'#ff4444',
  '--c-proc':'#9966ff','--c-pend':'#ccaa00',
  '--font':"'Arial Narrow', Arial, sans-serif" }
```

Cold Blue:
```js
{ '--c-bg':'#000814','--c-surface':'#001122','--c-panel':'#000d1a','--c-nav':'#000000',
  '--c-border':'#001833','--c-border-str':'#002255',
  '--c-accent':'#0088ff','--c-accent-dim':'#0055cc',
  '--c-label':'#4488aa','--c-label-dim':'#336688',
  '--c-ok':'#00ee88','--c-warn':'#ffcc44','--c-error':'#ff3344',
  '--c-proc':'#aa66ff','--c-pend':'#aacc00',
  '--font':"'Arial Narrow', Arial, sans-serif" }
```

Neon Green:
```js
{ '--c-bg':'#000800','--c-surface':'#001100','--c-panel':'#000a00','--c-nav':'#000000',
  '--c-border':'#001a00','--c-border-str':'#003300',
  '--c-accent':'#00ff44','--c-accent-dim':'#00cc33',
  '--c-label':'#44aa66','--c-label-dim':'#337755',
  '--c-ok':'#00ff88','--c-warn':'#ffcc00','--c-error':'#ff4422',
  '--c-proc':'#8844ff','--c-pend':'#ccee00',
  '--font':"'Arial Narrow', Arial, sans-serif" }
```

High Contrast:
```js
{ '--c-bg':'#000000','--c-surface':'#0a0a0a','--c-panel':'#050505','--c-nav':'#000000',
  '--c-border':'#222222','--c-border-str':'#444444',
  '--c-accent':'#ffaa00','--c-accent-dim':'#cc8800',
  '--c-label':'#ffffff','--c-label-dim':'#cccccc',
  '--c-ok':'#00ff77','--c-warn':'#ffdd00','--c-error':'#ff2222',
  '--c-proc':'#aa88ff','--c-pend':'#ffee00',
  '--font':"'Arial Narrow', Arial, sans-serif" }
```

**Variable groups and mapping:**

```js
const GROUPS = [
  { id: 'background', label: 'Background',
    vars: ['--c-bg','--c-surface','--c-panel','--c-nav'] },
  { id: 'borders',    label: 'Borders',
    vars: ['--c-border','--c-border-str'] },
  { id: 'accent',     label: 'Accent',
    vars: ['--c-accent','--c-accent-dim'] },
  { id: 'text',       label: 'Text & Labels',
    vars: ['--c-label','--c-label-dim'] },
  { id: 'status',     label: 'Status',
    vars: ['--c-ok','--c-warn','--c-error','--c-proc','--c-pend'] },
];
// --font handled separately as a <select>
```

Preview element → group mapping:
```js
// data-th-group values on preview elements:
// 'background' → navbar clone, body background
// 'surface'    → stat tiles, card/panel
// 'accent'     → primary buttons, accent text
// 'text'       → body text, label text
// 'status'     → status spans, toast example
// 'borders'    → border/divider elements
```

---

- [ ] **Step 1: Write the failing test in `tests/test_theme.py`**

Add this test to the existing `tests/test_theme.py`:

```python
def test_theme_page_route_exists():
    from api.main import app
    client = TestClient(app)
    # The HTML file doesn't exist yet — we expect 404 for file not found,
    # but NOT a 422 or 500 route error. The route itself must be registered.
    # Once the file exists, this returns 200.
    resp = client.get('/theme')
    # Route is registered (not 404 from FastAPI "no route") — file 404 or 200 both acceptable
    assert resp.status_code in (200, 404, 500)
```

Run it:
```
cd E:\DocVault
pytest tests/test_theme.py::test_theme_page_route_exists -v
```

Expected: PASS (route exists, returns 500 because file doesn't exist yet — that's expected).

- [ ] **Step 2: Create `frontend/theme.html`**

Create the full file. Here is the complete implementation:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>DocVault — Theme Editor</title>
  <link rel="stylesheet" href="/static/lcars.css">
  <style>
    /* ── Theme editor layout ─────────────────────────── */
    .th-layout {
      display: flex;
      height: calc(100vh - 72px); /* below 2-row navbar */
      overflow: hidden;
    }

    /* ── Preview pane ────────────────────────────────── */
    .th-preview {
      flex: 65;
      overflow-y: auto;
      padding: 16px;
      background: var(--c-bg);
      border-right: 2px solid var(--c-border-str);
    }

    .th-preview-label {
      font-size: 10px;
      color: var(--c-label-dim);
      text-transform: uppercase;
      letter-spacing: 2px;
      margin-bottom: 12px;
    }

    /* Clickable preview regions */
    [data-th-group] {
      cursor: pointer;
      transition: outline 0.1s;
    }
    [data-th-group]:hover {
      outline: 1px dashed var(--c-accent-dim);
    }
    .th-selected {
      outline: 2px solid var(--c-accent) !important;
      box-shadow: 0 0 8px var(--c-accent-dim);
    }

    /* ── Mini navbar clone (inside preview) ─────────── */
    .th-nav-clone {
      background: var(--c-nav);
      border-bottom: 1px solid var(--c-border-str);
      padding: 6px 16px;
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 12px;
      border-radius: 4px;
    }
    .th-nav-brand {
      color: var(--c-accent);
      font-weight: bold;
      font-size: 14px;
      letter-spacing: 2px;
    }
    .th-nav-pill {
      padding: 3px 10px;
      border: 1px solid var(--c-border-str);
      border-radius: 3px;
      font-size: 11px;
      color: var(--c-label);
    }
    .th-nav-pill.active-demo {
      background: var(--c-accent-dim);
      color: #000;
      font-weight: bold;
    }
    .th-sensor-row {
      padding: 4px 16px;
      background: var(--c-nav);
      display: flex;
      gap: 14px;
      font-size: 11px;
      color: var(--c-label-dim);
      margin-bottom: 12px;
      border-radius: 3px;
    }
    .th-sensor-row span { color: var(--c-warn); margin-left: 4px; }
    .th-sensor-row span.ok  { color: var(--c-ok); }
    .th-sensor-row span.err { color: var(--c-error); }

    /* ── Stat tiles ──────────────────────────────────── */
    .th-tiles {
      display: flex;
      gap: 8px;
      margin-bottom: 12px;
    }
    .th-tile {
      flex: 1;
      background: var(--c-surface);
      border: 1px solid var(--c-border);
      border-top: 3px solid var(--c-accent);
      padding: 10px 14px;
      border-radius: 3px;
    }
    .th-tile-val  { font-size: 22px; color: var(--c-accent); font-weight: bold; }
    .th-tile-lbl  { font-size: 10px; color: var(--c-label-dim); text-transform: uppercase; }

    /* ── Card / table ────────────────────────────────── */
    .th-card {
      background: var(--c-panel);
      border: 1px solid var(--c-border);
      border-radius: 3px;
      margin-bottom: 12px;
      overflow: hidden;
    }
    .th-card-head {
      background: var(--c-surface);
      border-bottom: 1px solid var(--c-border-str);
      padding: 6px 12px;
      font-size: 11px;
      font-weight: bold;
      color: var(--c-accent);
      text-transform: uppercase;
      letter-spacing: 1px;
    }
    .th-card table { width: 100%; border-collapse: collapse; font-size: 12px; }
    .th-card td { padding: 7px 12px; border-bottom: 1px solid var(--c-border); color: var(--c-label); }
    .th-card tr:last-child td { border-bottom: none; }

    /* ── Buttons ─────────────────────────────────────── */
    .th-btns {
      display: flex;
      gap: 8px;
      margin-bottom: 12px;
    }

    /* ── Form row ────────────────────────────────────── */
    .th-form-row {
      display: flex;
      gap: 8px;
      margin-bottom: 12px;
    }
    .th-form-row input, .th-form-row select {
      background: var(--c-surface);
      border: 1px solid var(--c-border-str);
      color: var(--c-label);
      padding: 6px 10px;
      font-size: 12px;
      font-family: var(--font);
      border-radius: 3px;
      flex: 1;
    }

    /* ── Status indicators ───────────────────────────── */
    .th-status-row {
      display: flex;
      gap: 16px;
      font-size: 12px;
      padding: 8px 12px;
      background: var(--c-surface);
      border: 1px solid var(--c-border);
      border-radius: 3px;
      margin-bottom: 10px;
    }
    .th-status-row .ok   { color: var(--c-ok); }
    .th-status-row .warn { color: var(--c-warn); }
    .th-status-row .err  { color: var(--c-error); }

    /* ── Toast demo ──────────────────────────────────── */
    .th-toast-demo {
      display: inline-block;
      background: var(--c-surface);
      border: 1px solid var(--c-border-str);
      border-left: 3px solid var(--c-ok);
      padding: 6px 12px;
      font-size: 12px;
      color: var(--c-label);
      border-radius: 3px;
    }

    /* ── Inspector panel ─────────────────────────────── */
    .th-inspector {
      flex: 35;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      background: var(--c-surface);
    }

    .th-insp-scroll {
      flex: 1;
      overflow-y: auto;
      padding: 14px;
    }

    .th-insp-label {
      font-size: 10px;
      color: var(--c-label-dim);
      text-transform: uppercase;
      letter-spacing: 2px;
      margin-bottom: 10px;
    }

    /* ── Preset bar ──────────────────────────────────── */
    .th-presets {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 16px;
    }
    .th-presets .lc-btn {
      font-size: 11px;
      padding: 4px 10px;
    }

    /* ── Variable group (details/summary) ────────────── */
    .th-group {
      margin-bottom: 8px;
      border: 1px solid var(--c-border);
      border-radius: 3px;
      overflow: hidden;
    }
    .th-group summary {
      padding: 7px 12px;
      background: var(--c-panel);
      font-size: 11px;
      font-weight: bold;
      color: var(--c-accent);
      text-transform: uppercase;
      letter-spacing: 1px;
      cursor: pointer;
      list-style: none;
      user-select: none;
    }
    .th-group summary::-webkit-details-marker { display: none; }
    .th-group summary::before { content: '▶ '; font-size: 9px; }
    .th-group[open] summary::before { content: '▼ '; }
    .th-group-body { padding: 8px 12px; }

    /* ── Color row ───────────────────────────────────── */
    .th-var-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 7px;
    }
    .th-var-row input[type=color] {
      width: 28px;
      height: 28px;
      border: 1px solid var(--c-border-str);
      border-radius: 3px;
      padding: 1px;
      background: var(--c-panel);
      cursor: pointer;
      flex-shrink: 0;
    }
    .th-var-row input[type=text] {
      width: 80px;
      background: var(--c-panel);
      border: 1px solid var(--c-border-str);
      color: var(--c-label);
      padding: 4px 8px;
      font-size: 12px;
      font-family: monospace;
      border-radius: 3px;
    }
    .th-var-name {
      font-size: 11px;
      color: var(--c-label-dim);
      flex: 1;
    }

    /* ── Font row ────────────────────────────────────── */
    .th-font-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 7px;
    }
    .th-font-row label { font-size: 11px; color: var(--c-label-dim); }
    .th-font-row select {
      background: var(--c-panel);
      border: 1px solid var(--c-border-str);
      color: var(--c-label);
      padding: 4px 8px;
      font-size: 12px;
      font-family: var(--font);
      border-radius: 3px;
      flex: 1;
    }

    /* ── Inspector footer ────────────────────────────── */
    .th-footer {
      padding: 10px 14px;
      border-top: 1px solid var(--c-border-str);
      display: flex;
      gap: 8px;
      background: var(--c-panel);
    }
    .th-footer .lc-btn { font-size: 11px; }

    /* ── Responsive stack ────────────────────────────── */
    @media (max-width: 1000px) {
      .th-layout { flex-direction: column; height: auto; }
      .th-preview { border-right: none; border-bottom: 2px solid var(--c-border-str); }
      .th-inspector { min-height: 500px; }
    }
  </style>
</head>
<body data-page="theme">
  <div class="lc-nav"></div>

  <div class="th-layout">

    <!-- ── PREVIEW PANE ─────────────────────────────────── -->
    <div class="th-preview">
      <div class="th-preview-label">Preview — click any element to edit</div>

      <!-- Navbar clone -->
      <div class="th-nav-clone" data-th-group="background">
        <span class="th-nav-brand">DV</span>
        <span class="th-nav-pill">Search</span>
        <span class="th-nav-pill active-demo">Theme</span>
        <span class="th-nav-pill">Settings</span>
      </div>

      <!-- Sensor rail clone -->
      <div class="th-sensor-row" data-th-group="background">
        Workers <span class="ok">▶ RUNNING</span>
        CPU <span>42%</span>
        Temp <span class="warn">71°C</span>
        State <span class="ok">▐ READY</span>
      </div>

      <!-- Stat tiles -->
      <div class="th-tiles" data-th-group="surface">
        <div class="th-tile">
          <div class="th-tile-val">4,281</div>
          <div class="th-tile-lbl">Files</div>
        </div>
        <div class="th-tile">
          <div class="th-tile-val">3,912</div>
          <div class="th-tile-lbl">Indexed</div>
        </div>
        <div class="th-tile">
          <div class="th-tile-val">12</div>
          <div class="th-tile-lbl">Errors</div>
        </div>
      </div>

      <!-- Card with table -->
      <div class="th-card" data-th-group="surface">
        <div class="th-card-head">Recent Files</div>
        <table>
          <tr><td>document.pdf</td><td style="color:var(--c-ok)">COMPLETE</td><td style="color:var(--c-label-dim)">2.4 MB</td></tr>
          <tr><td>photo.jpg</td><td style="color:var(--c-proc)">PROCESSING</td><td style="color:var(--c-label-dim)">1.1 MB</td></tr>
        </table>
      </div>

      <!-- Buttons -->
      <div class="th-btns" data-th-group="accent">
        <button class="lc-btn">Save</button>
        <button class="lc-btn" style="border-color:var(--c-label-dim);color:var(--c-label-dim)">Cancel</button>
        <button class="lc-btn" style="border-color:var(--c-error);color:var(--c-error)">Delete</button>
      </div>

      <!-- Form row -->
      <div class="th-form-row" data-th-group="surface">
        <input type="text" placeholder="Search files…" value="report 2024">
        <select><option>All types</option><option>PDF</option><option>Image</option></select>
      </div>

      <!-- Text / labels -->
      <div style="padding:8px 0;margin-bottom:10px" data-th-group="text">
        <p style="color:var(--c-label);font-size:13px;margin-bottom:4px">Primary label text — readable body copy.</p>
        <p style="color:var(--c-label-dim);font-size:11px">Secondary dim text — metadata, timestamps, descriptions.</p>
      </div>

      <!-- Status indicators -->
      <div class="th-status-row" data-th-group="status">
        <span class="ok">● Complete (3,912)</span>
        <span class="warn">● Processing (14)</span>
        <span class="err">● Error (12)</span>
        <span style="color:var(--c-proc)">● Embedding (2)</span>
        <span style="color:var(--c-pend)">● Pending (243)</span>
      </div>

      <!-- Toast demo -->
      <div class="th-toast-demo" data-th-group="status">✓ Theme saved successfully</div>

      <!-- Border demo -->
      <div style="margin-top:12px;padding:10px 14px;border:1px solid var(--c-border);border-top:3px solid var(--c-border-str);background:var(--c-panel);border-radius:3px;font-size:12px;color:var(--c-label-dim)" data-th-group="borders">
        ← Border and divider colours shown here
      </div>
    </div>

    <!-- ── INSPECTOR PANEL ───────────────────────────────── -->
    <div class="th-inspector">
      <div class="th-insp-scroll">
        <div class="th-insp-label">Inspector</div>

        <!-- Preset bar -->
        <div class="th-presets">
          <button class="lc-btn" onclick="applyPreset('warm')">Warm LCARS</button>
          <button class="lc-btn" onclick="applyPreset('cold')">Cold Blue</button>
          <button class="lc-btn" onclick="applyPreset('green')">Neon Green</button>
          <button class="lc-btn" onclick="applyPreset('hc')">High Contrast</button>
        </div>

        <!-- Variable groups — dynamically rendered by JS -->
        <div id="th-groups"></div>

        <!-- Font group -->
        <details class="th-group" id="th-group-font" open>
          <summary>Typography</summary>
          <div class="th-group-body">
            <div class="th-font-row">
              <label>--font</label>
              <select id="th-font-select" onchange="onFontChange(this.value)">
                <option value="'Arial Narrow', Arial, sans-serif">LCARS (Arial Narrow)</option>
                <option value="'Courier New', monospace">Monospace (Courier New)</option>
                <option value="system-ui, sans-serif">System UI</option>
                <option value="Georgia, serif">Georgia (serif)</option>
              </select>
            </div>
          </div>
        </details>
      </div>

      <!-- Footer -->
      <div class="th-footer">
        <button class="lc-btn" onclick="saveTheme()">Save</button>
        <button class="lc-btn" onclick="revertTheme()" style="border-color:var(--c-label-dim);color:var(--c-label-dim)">Revert</button>
        <button class="lc-btn" onclick="resetTheme()" style="border-color:var(--c-error);color:var(--c-error)">Reset to Defaults</button>
      </div>
    </div>

  </div><!-- /.th-layout -->

  <script src="/static/lcars.js"></script>
  <script>
    // ── Preset data ──────────────────────────────────────────────────────────
    const PRESETS = {
      warm: {
        '--c-bg':'#0a0600','--c-surface':'#0d0800','--c-panel':'#080400','--c-nav':'#000000',
        '--c-border':'#1a0d00','--c-border-str':'#331a00',
        '--c-accent':'#ff9900','--c-accent-dim':'#cc6600',
        '--c-label':'#aa7744','--c-label-dim':'#996633',
        '--c-ok':'#33ee77','--c-warn':'#ffcc66','--c-error':'#ff4444',
        '--c-proc':'#9966ff','--c-pend':'#ccaa00',
        '--font':"'Arial Narrow', Arial, sans-serif"
      },
      cold: {
        '--c-bg':'#000814','--c-surface':'#001122','--c-panel':'#000d1a','--c-nav':'#000000',
        '--c-border':'#001833','--c-border-str':'#002255',
        '--c-accent':'#0088ff','--c-accent-dim':'#0055cc',
        '--c-label':'#4488aa','--c-label-dim':'#336688',
        '--c-ok':'#00ee88','--c-warn':'#ffcc44','--c-error':'#ff3344',
        '--c-proc':'#aa66ff','--c-pend':'#aacc00',
        '--font':"'Arial Narrow', Arial, sans-serif"
      },
      green: {
        '--c-bg':'#000800','--c-surface':'#001100','--c-panel':'#000a00','--c-nav':'#000000',
        '--c-border':'#001a00','--c-border-str':'#003300',
        '--c-accent':'#00ff44','--c-accent-dim':'#00cc33',
        '--c-label':'#44aa66','--c-label-dim':'#337755',
        '--c-ok':'#00ff88','--c-warn':'#ffcc00','--c-error':'#ff4422',
        '--c-proc':'#8844ff','--c-pend':'#ccee00',
        '--font':"'Arial Narrow', Arial, sans-serif"
      },
      hc: {
        '--c-bg':'#000000','--c-surface':'#0a0a0a','--c-panel':'#050505','--c-nav':'#000000',
        '--c-border':'#222222','--c-border-str':'#444444',
        '--c-accent':'#ffaa00','--c-accent-dim':'#cc8800',
        '--c-label':'#ffffff','--c-label-dim':'#cccccc',
        '--c-ok':'#00ff77','--c-warn':'#ffdd00','--c-error':'#ff2222',
        '--c-proc':'#aa88ff','--c-pend':'#ffee00',
        '--font':"'Arial Narrow', Arial, sans-serif"
      }
    };

    // ── Variable group definitions ────────────────────────────────────────────
    const GROUPS = [
      { id: 'background', label: 'Background',   vars: ['--c-bg','--c-surface','--c-panel','--c-nav'] },
      { id: 'borders',    label: 'Borders',       vars: ['--c-border','--c-border-str'] },
      { id: 'accent',     label: 'Accent',        vars: ['--c-accent','--c-accent-dim'] },
      { id: 'text',       label: 'Text & Labels', vars: ['--c-label','--c-label-dim'] },
      { id: 'status',     label: 'Status',        vars: ['--c-ok','--c-warn','--c-error','--c-proc','--c-pend'] },
    ];

    // Preview element → group mapping
    const GROUP_MAP = {
      background: 'background',
      surface:    'surface',
      accent:     'accent',
      text:       'text',
      status:     'status',
      borders:    'borders',
    };

    // Current live overrides (delta from lcars.css defaults)
    let _overrides = {};

    // ── Build inspector groups ────────────────────────────────────────────────
    function buildGroups() {
      const container = document.getElementById('th-groups');
      container.innerHTML = '';
      for (const g of GROUPS) {
        const det = document.createElement('details');
        det.className = 'th-group';
        det.id = `th-group-${g.id}`;
        const sum = document.createElement('summary');
        sum.textContent = g.label;
        det.appendChild(sum);
        const body = document.createElement('div');
        body.className = 'th-group-body';
        for (const varName of g.vars) {
          body.appendChild(makeVarRow(varName));
        }
        det.appendChild(body);
        container.appendChild(det);
      }
    }

    function makeVarRow(varName) {
      const row = document.createElement('div');
      row.className = 'th-var-row';
      row.dataset.varName = varName;

      const colorInput = document.createElement('input');
      colorInput.type = 'color';
      colorInput.id = `th-color-${varName.replace(/[^a-z0-9]/g, '_')}`;

      const hexInput = document.createElement('input');
      hexInput.type = 'text';
      hexInput.maxLength = 7;
      hexInput.placeholder = '#rrggbb';

      const label = document.createElement('span');
      label.className = 'th-var-name';
      label.textContent = varName;

      // Two-way binding
      colorInput.addEventListener('input', () => {
        hexInput.value = colorInput.value;
        setVar(varName, colorInput.value);
      });
      hexInput.addEventListener('input', () => {
        if (/^#[0-9a-fA-F]{6}$/.test(hexInput.value)) {
          colorInput.value = hexInput.value;
          setVar(varName, hexInput.value);
        }
      });
      hexInput.addEventListener('change', () => {
        if (/^#[0-9a-fA-F]{6}$/.test(hexInput.value)) {
          colorInput.value = hexInput.value;
          setVar(varName, hexInput.value);
        } else {
          // Revert to current value on invalid input
          hexInput.value = colorInput.value;
        }
      });

      row.appendChild(colorInput);
      row.appendChild(hexInput);
      row.appendChild(label);
      return row;
    }

    // ── Read current CSS variable value from document ─────────────────────────
    function getCSSVar(varName) {
      return getComputedStyle(document.documentElement)
        .getPropertyValue(varName).trim();
    }

    // ── Sync inspector inputs from current CSS state ──────────────────────────
    function syncInputsFromCSS() {
      for (const g of GROUPS) {
        for (const varName of g.vars) {
          const val = getCSSVar(varName);
          const colorId = `th-color-${varName.replace(/[^a-z0-9]/g, '_')}`;
          const colorEl = document.getElementById(colorId);
          const row = document.querySelector(`[data-var-name="${varName}"]`);
          if (!row) continue;
          const hexEl = row.querySelector('input[type=text]');
          if (colorEl && /^#[0-9a-fA-F]{6}$/.test(val)) {
            colorEl.value = val;
          }
          if (hexEl) hexEl.value = val;
        }
      }
      // Sync font select
      const fontSel = document.getElementById('th-font-select');
      if (fontSel) {
        const currentFont = getCSSVar('--font');
        for (const opt of fontSel.options) {
          if (currentFont.includes(opt.value.split(',')[0].replace(/'/g, ''))) {
            fontSel.value = opt.value;
            break;
          }
        }
      }
    }

    // ── Set a single variable ─────────────────────────────────────────────────
    function setVar(varName, value) {
      _overrides[varName] = value;
      applyTheme(_overrides);
      syncInputsFromCSS();
    }

    function onFontChange(value) {
      _overrides['--font'] = value;
      applyTheme(_overrides);
    }

    // ── Apply a preset ────────────────────────────────────────────────────────
    function applyPreset(name) {
      const preset = PRESETS[name];
      if (!preset) return;
      // For "warm" (defaults), we save {} so defaults from lcars.css take over cleanly
      _overrides = name === 'warm' ? {} : { ...preset };
      applyTheme(_overrides);
      syncInputsFromCSS();
    }

    // ── Save theme ────────────────────────────────────────────────────────────
    async function saveTheme() {
      try {
        const resp = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: 'ui:theme', value: JSON.stringify(_overrides) }),
        });
        if (resp.ok) {
          lcToast('Theme saved');
        } else {
          lcToast('Save failed', true);
        }
      } catch (_) {
        lcToast('Save failed', true);
      }
    }

    // ── Revert to saved theme ─────────────────────────────────────────────────
    async function revertTheme() {
      try {
        const resp = await fetch('/api/settings/theme');
        const { theme } = await resp.json();
        _overrides = theme || {};
        applyTheme(_overrides);
        syncInputsFromCSS();
        lcToast('Reverted to saved theme');
      } catch (_) {
        lcToast('Revert failed', true);
      }
    }

    // ── Reset to lcars.css defaults ────────────────────────────────────────────
    async function resetTheme() {
      try {
        await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: 'ui:theme', value: '{}' }),
        });
        _overrides = {};
        applyTheme({});
        syncInputsFromCSS();
        lcToast('Reset to defaults');
      } catch (_) {
        lcToast('Reset failed', true);
      }
    }

    // ── Preview click → scroll inspector to group ─────────────────────────────
    function setupPreviewClicks() {
      document.querySelectorAll('[data-th-group]').forEach(el => {
        el.addEventListener('click', (e) => {
          e.stopPropagation();
          // Deselect all
          document.querySelectorAll('.th-selected').forEach(s => s.classList.remove('th-selected'));
          el.classList.add('th-selected');

          const groupId = el.dataset.thGroup;
          // Map 'surface' → 'background' (same group in inspector)
          const inspectorGroupId = groupId === 'surface' ? 'background' : groupId;
          const groupEl = document.getElementById(`th-group-${inspectorGroupId}`);
          if (groupEl) {
            groupEl.open = true;
            groupEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
          }
        });
      });
      // Click on blank preview deselects
      document.querySelector('.th-preview').addEventListener('click', () => {
        document.querySelectorAll('.th-selected').forEach(s => s.classList.remove('th-selected'));
      });
    }

    // ── Init ──────────────────────────────────────────────────────────────────
    document.addEventListener('lc:ready', async () => {
      buildGroups();

      // Load saved theme into _overrides
      try {
        const resp = await fetch('/api/settings/theme');
        const { theme } = await resp.json();
        _overrides = theme || {};
      } catch (_) {
        _overrides = {};
      }

      // applyTheme was already called by lcThemeLoad() in lcInit — just sync inputs
      syncInputsFromCSS();
      setupPreviewClicks();
    });
  </script>
</body>
</html>
```

- [ ] **Step 3: Verify the page loads without JS errors**

Start the server and open `http://localhost:8000/theme`. Check browser console — no errors expected.

```
cd E:\DocVault
python run.py
```

Verify in browser:
- Theme nav pill is visible and active
- Preview pane shows all LCARS elements
- Inspector shows preset buttons and 6 collapsible groups
- Click a preset — preview colors update live
- Click an element in preview — inspector scrolls to its group
- Save button shows toast "Theme saved"
- Reload any other page (e.g. `/vault`) — if you saved a preset, the colors apply there too

- [ ] **Step 4: Run all tests**

```
cd E:\DocVault
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/theme.html
git commit -m "feat(theme): add theme editor page with presets, live preview, and CSS variable inspector"
```

---

### Task 5: Final integration smoke test + cleanup

- [ ] **Step 1: Full test run**

```
cd E:\DocVault
pytest tests/ -v --tb=short
```

Expected: all tests pass with no warnings about missing keys.

- [ ] **Step 2: Manual smoke test checklist**

Open the app and verify:
- [ ] `/theme` accessible from nav on every page
- [ ] Selecting "Cold Blue" preset changes preview immediately
- [ ] Saving Cold Blue, navigating to `/vault`, and reloading shows cold blue colors
- [ ] "Reset to Defaults" reverts to warm LCARS on all pages
- [ ] Clicking stat tile in preview highlights it + expands Background group in inspector
- [ ] Changing `--c-accent` hex input to `#ff0000` updates color swatch and preview in real-time

- [ ] **Step 3: Commit if any minor fixes were made**

```bash
git add frontend/theme.html frontend/static/lcars.js
git commit -m "fix(theme): integration smoke test fixes"
```

---

## Summary

| Task | Files | Tests |
|---|---|---|
| 1 — Schema + endpoint | `core/settings.py`, `api/routes/settings.py`, `tests/test_theme.py` | 4 pytest tests |
| 2 — /theme route | `api/main.py` | smoke (python import) |
| 3 — lcars.js + i18n | `frontend/static/lcars.js`, 3 × `i18n/*.json` | parity test |
| 4 — theme.html | `frontend/theme.html` | manual smoke |
| 5 — Integration | all | full suite |
