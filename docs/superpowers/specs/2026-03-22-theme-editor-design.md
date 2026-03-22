# Theme Editor Design

**Date:** 2026-03-22

---

## Goal

Add a `/theme` page to DocVault that lets users visually select and customise the LCARS color scheme and font. Changes apply live across all pages via CSS custom properties stored in settings.db.

---

## Architecture

A new standalone page (`frontend/theme.html`) with a side-by-side layout: a static LCARS preview pane on the left and a property inspector on the right. All edits are applied live by injecting a `<style id="th-override">:root { ... }</style>` into the page head. Saving POSTs to the existing `/api/settings` endpoint. Every other page fetches the saved theme during `lcInit()` via a new lean `GET /api/settings/theme` endpoint and injects the same style block.

---

## Page Layout

```
┌─────────────────────────────────────────────────────────┐
│  LCARS Navbar (shared, injected by lcars.js)            │
├───────────────────────────────────┬─────────────────────┤
│                                   │                     │
│         PREVIEW PANE              │  INSPECTOR PANEL    │
│            (~65%)                 │      (~35%)         │
│                                   │                     │
└───────────────────────────────────┴─────────────────────┘
```

Both panes fill available height below the navbar. On screens narrower than 1000 px the layout stacks (preview on top, inspector below).

---

## Preview Pane

A static HTML snapshot using real LCARS classes — not a live page, just representative elements sufficient to make every CSS variable visible:

- **Navbar clone** — logo wordmark, 3 nav pills, sensor rail with dummy values
- **Stat tiles** — 3 tiles (matching vault.html tile style)
- **Card with table** — a `.lc-panel` card containing a 2-row table
- **Button row** — primary (`.lc-btn`), secondary (`.lc-btn.secondary`), danger (`.lc-btn.danger`)
- **Form element** — one text `<input>` and one `<select>`
- **Badge** — one `.lc-bar-badge` label
- **Toast example** — one static `.lc-toast` element (no timer)
- **Status indicators** — one each of `.ok`, `.warn`, `.err` sensor spans

Each clickable region carries a `data-th-group` attribute mapping it to a variable group in the inspector:

| Element | `data-th-group` |
|---|---|
| Navbar, sensor rail | `background` |
| Stat tiles, card, panel | `surface` |
| Primary button, accent text | `accent` |
| Body text, labels, dim text | `text` |
| Status spans, toast | `status` |
| Borders, dividers | `borders` |

Clicking a region adds a `.th-selected` CSS ring (outline + box-shadow in `--c-accent`) and scrolls the inspector to the matching group, which expands if collapsed. Clicking blank space deselects.

---

## Inspector Panel

### Preset Bar

Four named presets rendered as `.lc-btn` buttons at the top of the inspector. Clicking a preset fills all variable inputs and applies the override immediately:

| Preset | Character |
|---|---|
| **Warm LCARS** | Current defaults — deep brown-blacks, amber accent |
| **Cold Blue** | Dark navy background, electric blue accent |
| **Neon Green** | Near-black background, neon green accent |
| **High Contrast** | Pure black background, white text, bright amber accent |

The preset values are hardcoded in `theme.html`'s script block (no server round-trip needed).

### Variable Groups

Six collapsible groups (`<details>` elements) below the preset bar. Each group auto-expands when its region is selected in the preview:

**Background**
- `--c-bg` — page background
- `--c-surface` — elevated surfaces
- `--c-panel` — card/panel fill
- `--c-nav` — navbar background

**Borders**
- `--c-border` — subtle border
- `--c-border-str` — strong border / dividers

**Accent**
- `--c-accent` — primary accent (orange by default)
- `--c-accent-dim` — dimmed accent (active pills, hover)

**Text & Labels**
- `--c-label` — primary label text
- `--c-label-dim` — secondary / dim label text

**Status**
- `--c-ok` — success green
- `--c-warn` — warning amber
- `--c-error` — error red
- `--c-proc` — processing purple
- `--c-pend` — pending yellow

**Typography**
- `--font` — rendered as a `<select>`: `'Arial Narrow', Arial, sans-serif` (default) | `'Courier New', monospace` | `system-ui, sans-serif` | `Georgia, serif`

### Variable Row

Each color variable is rendered as:

```
[color swatch]  [hex input #rrggbb]  --c-accent
```

- **Color swatch**: a `<input type="color">` styled as a 24×24 square; clicking opens the native OS picker
- **Hex input**: a `<input type="text" maxlength="7">` — editing here updates the color picker and live preview
- Both inputs are two-way bound: change in either updates the other and fires the live style injection

### Inspector Footer

Three buttons pinned to the bottom of the inspector:

- **Save** — POST `{key: 'ui:theme', value: JSON.stringify(currentOverrides)}` to `/api/settings`; shows `lcToast('Theme saved')` on success
- **Revert** — re-fetch saved theme from `GET /api/settings/theme` and re-apply (discards unsaved changes)
- **Reset to defaults** — save `{}` (empty override), reload

---

## Live Style Injection

All changes are applied by replacing or creating `<style id="th-override">` in `document.head`:

```js
function applyTheme(overrides) {
  const vars = Object.entries(overrides)
    .map(([k, v]) => `  ${k}: ${v};`)
    .join('\n');
  let el = document.getElementById('th-override');
  if (!el) { el = document.createElement('style'); el.id = 'th-override'; document.head.appendChild(el); }
  el.textContent = vars ? `:root {\n${vars}\n}` : '';
}
```

The preview pane is in the same document, so the injected `:root` variables immediately affect all preview elements.

---

## Cross-Page Theme Application

`lcars.js` gains a theme fetch step inside `lcInit()` (after i18n, before dispatching `lc:ready`):

```js
async function lcThemeLoad() {
  try {
    const res = await fetch('/api/settings/theme');
    const { theme } = await res.json();
    if (theme && Object.keys(theme).length > 0) applyTheme(theme);
  } catch (_) { /* non-fatal */ }
}
```

`applyTheme` is the same function shown above, extracted to a shared utility. If the endpoint returns `{}` or fails, no style tag is injected and `lcars.css` defaults remain untouched.

---

## Backend

### New endpoint: `GET /api/settings/theme`

Added to `api/routes/settings.py`:

```python
@router.get("/theme")
def get_theme():
    raw = settings.get("ui:theme", "{}")
    try:
        theme = json.loads(raw)
    except Exception:
        theme = {}
    return {"theme": theme}
```

Returns `{"theme": {...}}`. Called on every page load via `lcars.js`; intentionally lean (no auth, no side effects).

### Existing endpoint: `POST /api/settings`

No changes needed. The Save button posts `{key: "ui:theme", value: "{...}"}` using the same endpoint settings.html already uses.

### Settings schema: `core/settings.py`

New entry:

```python
'ui:theme': {
    'type': 'string',
    'default': '{}',
    'label': 'UI Theme Overrides',
    'group': 'ui',
    'description': 'JSON map of CSS custom property overrides applied to every page.'
}
```

---

## Navigation

The Theme page is added to the LCARS nav in `lcars.js` (the `NAV_ITEMS` / `buildNavHTML()` section):

```js
{ href: '/theme', label: () => t('nav.theme'), icon: '🎨' }
```

i18n key `nav.theme` added to all locale files (`en.json`: `"Theme"`, `es.json`: `"Tema"`, `fr.json`: `"Th\u00e8me"`).

---

## Files Changed

| File | Change |
|---|---|
| `frontend/theme.html` | New page |
| `frontend/static/lcars.js` | Add `lcThemeLoad()`, `applyTheme()`, nav item, `nav.theme` key consumption |
| `frontend/static/i18n/en.json` | Add `nav.theme` |
| `frontend/static/i18n/es.json` | Add `nav.theme` |
| `frontend/static/i18n/fr.json` | Add `nav.theme` |
| `api/routes/settings.py` | Add `GET /api/settings/theme` |
| `core/settings.py` | Add `ui:theme` schema entry |
| `tests/test_theme.py` | New: theme endpoint, settings schema, applyTheme logic |

---

## Testing

- `GET /api/settings/theme` returns `{"theme": {}}` when no theme saved
- `GET /api/settings/theme` returns correct dict after saving via `POST /api/settings`
- `ui:theme` present in settings schema with correct type and default
- `nav.theme` key present in all three locale files
- Preview pane: all `data-th-group` attributes map to valid group names
- Inspector: clicking a group region scrolls + expands correct group
- Live injection: changing a color variable updates `<style id="th-override">` correctly
- Preset: clicking Warm LCARS clears all overrides; Cold Blue applies all expected values
- Save → reload: theme persists and is applied by `lcars.js` on next page load
- Reset to defaults: saves `{}`, reloads, no `th-override` style injected

---

## Non-Goals

- Per-element overrides (font size, border radius on individual components) — not in scope
- Theme export/import — not in scope
- Theme versioning or named user themes — not in scope
- Dark/light mode toggle — the LCARS design is inherently dark; presets cover contrast needs
