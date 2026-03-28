# Font Scale Control Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a proportional font scale control (+/− stepper) to the theme editor that uniformly scales all UI text via a single CSS variable.

**Architecture:** Add `--font-scale: 1` to `:root` in `lcars.css` and wrap all 25 `font-size: Xpx` rules in `calc(Xpx * var(--font-scale))`. In `theme.html`, add a +/− stepper that saves `--font-scale` into `_overrides` like any other CSS variable. The existing `applyTheme()` machinery in `lcars.js` handles persistence automatically.

**Tech Stack:** CSS custom properties, `calc()`, vanilla JS; no new dependencies.

---

## Chunk 1: CSS layer

### Task 1: Add `--font-scale` variable and wrap all font-size rules

**Files:**
- Modify: `frontend/static/lcars.css:4-22` (`:root` block)
- Modify: `frontend/static/lcars.css` (25 `font-size:` rules throughout)
- Test: `tests/test_font_scale_css.py` (new)

---

- [ ] **Step 1: Write the failing test**

```python
# tests/test_font_scale_css.py
import re

CSS_PATH = "frontend/static/lcars.css"

def _read():
    with open(CSS_PATH) as f:
        return f.read()

def test_font_scale_var_in_root():
    """--font-scale must be declared in :root."""
    css = _read()
    # Find :root block
    root_match = re.search(r':root\s*\{([^}]+)\}', css)
    assert root_match, ":root block not found"
    assert '--font-scale' in root_match.group(1), "--font-scale not in :root"

def test_font_scale_default_is_one():
    """Default value must be 1 (no scaling)."""
    css = _read()
    assert re.search(r'--font-scale\s*:\s*1\b', css), "--font-scale default should be 1"

def test_no_bare_font_size_px():
    """All font-size: Xpx rules must use calc(). None should remain as bare px values."""
    css = _read()
    # Strip comments
    css_no_comments = re.sub(r'/\*.*?\*/', '', css, flags=re.DOTALL)
    bare = re.findall(r'font-size\s*:\s*\d+px', css_no_comments)
    assert bare == [], f"Found bare font-size px rules (should use calc): {bare}"

def test_all_font_size_use_font_scale():
    """All font-size rules must reference --font-scale."""
    css = _read()
    css_no_comments = re.sub(r'/\*.*?\*/', '', css, flags=re.DOTALL)
    rules = re.findall(r'font-size\s*:[^;]+;', css_no_comments)
    bad = [r for r in rules if '--font-scale' not in r]
    assert bad == [], f"font-size rules not using --font-scale: {bad}"
```

- [ ] **Step 2: Run tests to confirm they fail**

```
cd E:\DocVault && python -m pytest tests/test_font_scale_css.py -v
```
Expected: All 4 tests FAIL

- [ ] **Step 3: Add `--font-scale: 1` to `:root` in `lcars.css`**

In `frontend/static/lcars.css`, add `--font-scale: 1` as the last entry in `:root`:

```css
:root {
  --c-bg:         #0a0600;
  --c-surface:    #0d0800;
  --c-panel:      #080400;
  --c-nav:        #000000;
  --c-border:     #1a0d00;
  --c-border-str: #331a00;
  --c-accent:     #ff9900;
  --c-accent-dim: #cc6600;
  --c-label:      #aa7744;
  --c-label-dim:  #996633;
  --c-ok:         #33ee77;
  --c-warn:       #ffcc66;
  --c-error:      #ff4444;
  --c-proc:       #9966ff;
  --c-pend:       #ccaa00;
  --c-unk:        #666666;
  --font:         'Arial Narrow', Arial, sans-serif;
  --font-scale:   1;
}
```

- [ ] **Step 4: Convert all 25 `font-size: Xpx` rules to `calc()`**

Replace each `font-size: Xpx` in `lcars.css` with `font-size: calc(Xpx * var(--font-scale))`. **Important:** Multiple selectors share the same pixel value (e.g., four selectors use `10px`, many use `11px`). Always replace by selector context, not by pixel value alone — do not use a global search-replace on the pixel value string. The complete list of rules to change:

| Selector | Old | New |
|---|---|---|
| `.lc-elbow` | `font-size: 16px` | `font-size: calc(16px * var(--font-scale))` |
| `.lc-brand` | `font-size: 13px` | `font-size: calc(13px * var(--font-scale))` |
| `.lc-pill` | `font-size: 10px` | `font-size: calc(10px * var(--font-scale))` |
| `.lc-sensor` | `font-size: 11px` | `font-size: calc(11px * var(--font-scale))` |
| `.lc-section-title` | `font-size: 11px` | `font-size: calc(11px * var(--font-scale))` |
| `.lc-bar-tab` | `font-size: 20px` | `font-size: calc(20px * var(--font-scale))` |
| `.lc-bar-name` | `font-size: 14px` | `font-size: calc(14px * var(--font-scale))` |
| `.lc-bar-desc` | `font-size: 11px` | `font-size: calc(11px * var(--font-scale))` |
| `.lc-bar-arrow` | `font-size: 16px` | `font-size: calc(16px * var(--font-scale))` |
| `.lc-stat-val` | `font-size: 22px` | `font-size: calc(22px * var(--font-scale))` |
| `.lc-stat-lbl` | `font-size: 10px` | `font-size: calc(10px * var(--font-scale))` |
| `.lc-vault-name` | `font-size: 12px` | `font-size: calc(12px * var(--font-scale))` |
| `.lc-vault-path` | `font-size: 11px` | `font-size: calc(11px * var(--font-scale))` |
| `.lc-vault-meta` | `font-size: 11px` | `font-size: calc(11px * var(--font-scale))` |
| `.lc-badge` | `font-size: 10px` | `font-size: calc(10px * var(--font-scale))` |
| `.lc-filter-pill` | `font-size: 11px` | `font-size: calc(11px * var(--font-scale))` |
| `.lc-table` | `font-size: 12px` | `font-size: calc(12px * var(--font-scale))` |
| `.lc-table th` | `font-size: 11px` | `font-size: calc(11px * var(--font-scale))` |
| `.lc-divider-label` | `font-size: 10px` | `font-size: calc(10px * var(--font-scale))` |
| `.lc-btn` | `font-size: 11px` | `font-size: calc(11px * var(--font-scale))` |
| `.lc-btn-sm` | `font-size: 10px` | `font-size: calc(10px * var(--font-scale))` |
| `.lc-input, .lc-select` | `font-size: 12px` | `font-size: calc(12px * var(--font-scale))` |
| `.lc-toast` | `font-size: 12px` | `font-size: calc(12px * var(--font-scale))` |
| `.lc-modal-title` | `font-size: 13px` | `font-size: calc(13px * var(--font-scale))` |
| `.lc-ctx-item` | `font-size: 11px` | `font-size: calc(11px * var(--font-scale))` |

- [ ] **Step 5: Run tests to confirm they pass**

```
cd E:\DocVault && python -m pytest tests/test_font_scale_css.py -v
```
Expected: All 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/static/lcars.css tests/test_font_scale_css.py
git commit -m "feat(theme): add --font-scale CSS variable to lcars.css"
```

---

## Chunk 2: Theme editor UI

### Task 2: Add +/− stepper to theme editor

**Files:**
- Modify: `frontend/theme.html` (Typography section + JS)
- Test: `tests/test_font_scale_ui.py` (new)

---

- [ ] **Step 1: Write the failing test**

```python
# tests/test_font_scale_ui.py
"""
Parser-level checks on theme.html for the font scale stepper.
These verify structure without a browser.
"""
from pathlib import Path

HTML = Path("frontend/theme.html").read_text(encoding="utf-8")

def test_font_scale_stepper_html_present():
    """Stepper buttons and display element must exist."""
    assert 'id="th-scale-val"' in HTML, "Missing scale value display element"
    assert 'onFontScaleChange(-1)' in HTML, "Missing decrement button call"
    assert 'onFontScaleChange(1)' in HTML, "Missing increment button call"

def test_font_scale_js_function_present():
    """onFontScaleChange() JS function must be defined."""
    assert 'function onFontScaleChange(' in HTML, "Missing onFontScaleChange function"

def test_font_scale_uses_overrides():
    """JS function must save --font-scale into _overrides."""
    assert "_overrides['--font-scale']" in HTML, "Missing _overrides assignment for --font-scale"

def test_font_scale_range_clamped():
    """JS function must clamp to 80–140 range, verified within onFontScaleChange."""
    fn_idx = HTML.index('function onFontScaleChange(')
    # Slice to the next top-level function definition
    next_fn_idx = HTML.index('\n    function ', fn_idx + 1)
    fn_body = HTML[fn_idx:next_fn_idx]
    assert '0.8' in fn_body, "Min clamp value 0.8 not found in onFontScaleChange"
    assert '1.4' in fn_body, "Max clamp value 1.4 not found in onFontScaleChange"

def test_sync_inputs_updates_stepper():
    """syncInputsFromCSS must update the scale display."""
    assert 'th-scale-val' in HTML
    # Confirm syncInputsFromCSS references the display element
    sync_idx = HTML.index('function syncInputsFromCSS')
    # Delimit to next function definition at same indentation level
    next_fn_idx = HTML.index('\n    function ', sync_idx + 1)
    sync_body = HTML[sync_idx:next_fn_idx]
    assert 'th-scale-val' in sync_body, "syncInputsFromCSS does not update stepper display"
```

- [ ] **Step 2: Run tests to confirm they fail**

```
cd E:\DocVault && python -m pytest tests/test_font_scale_ui.py -v
```
Expected: All 5 tests FAIL

- [ ] **Step 3: Add stepper HTML to the Typography section in `theme.html`**

Find the Typography `<details>` block (around line 412) and add the scale row after the font select row:

**Before** (end of `.th-group-body`):
```html
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
```

**After**:
```html
            <div class="th-font-row">
              <label>--font</label>
              <select id="th-font-select" onchange="onFontChange(this.value)">
                <option value="'Arial Narrow', Arial, sans-serif">LCARS (Arial Narrow)</option>
                <option value="'Courier New', monospace">Monospace (Courier New)</option>
                <option value="system-ui, sans-serif">System UI</option>
                <option value="Georgia, serif">Georgia (serif)</option>
              </select>
            </div>
            <div class="th-font-row" style="margin-top:8px">
              <label>--font-scale</label>
              <div style="display:flex;align-items:center;gap:0;border:1px solid var(--c-border-str);border-radius:3px;overflow:hidden">
                <button class="lc-btn lc-btn-sm" style="border-radius:0;border:none;border-right:1px solid var(--c-border-str)" onclick="onFontScaleChange(-1)">−</button>
                <span id="th-scale-val" style="padding:3px 12px;font-size:12px;color:var(--c-label);background:var(--c-panel);min-width:46px;text-align:center">100%</span>
                <button class="lc-btn lc-btn-sm" style="border-radius:0;border:none;border-left:1px solid var(--c-border-str)" onclick="onFontScaleChange(1)">+</button>
              </div>
            </div>
          </div>
        </details>
```

- [ ] **Step 4: Add `onFontScaleChange()` JS function to `theme.html`**

Add after `onFontChange()` (around line 603):

```javascript
    function onFontScaleChange(direction) {
      const current = parseFloat(getCSSVar('--font-scale')) || 1;
      // Steps of 10% (0.1), clamped to 80%–140%
      const next = Math.min(1.4, Math.max(0.8, Math.round((current + direction * 0.1) * 10) / 10));
      _overrides['--font-scale'] = String(next);
      applyTheme(_overrides);
      syncInputsFromCSS();
    }
```

- [ ] **Step 5: Update `syncInputsFromCSS()` to sync the stepper display**

In `syncInputsFromCSS()`, add after the font select sync block (before the closing `}`):

```javascript
      // Sync font scale stepper
      const scaleEl = document.getElementById('th-scale-val');
      if (scaleEl) {
        const scaleVal = parseFloat(getCSSVar('--font-scale')) || 1;
        scaleEl.textContent = Math.round(scaleVal * 100) + '%';
      }
```

- [ ] **Step 6: Run tests to confirm they pass**

```
cd E:\DocVault && python -m pytest tests/test_font_scale_ui.py -v
```
Expected: All 5 tests PASS

- [ ] **Step 7: Run full test suite**

```
cd E:\DocVault && python -m pytest tests/ -v --tb=short
```
Expected: All existing tests pass, no regressions

- [ ] **Step 8: Commit**

```bash
git add frontend/theme.html tests/test_font_scale_ui.py
git commit -m "feat(theme): add font scale stepper to theme editor (+/- 10%, 80-140%)"
```
