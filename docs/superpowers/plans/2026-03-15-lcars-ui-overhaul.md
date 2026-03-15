# LCARS UI Overhaul Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Tailwind-based light UI with a unified Warm LCARS dark design system across all DocVault pages.

**Architecture:** Shared `lcars.css` + `lcars.js` loaded by every page. Each page's Tailwind CDN tag is removed and replaced with these two files. All existing JS polling, modals, and API calls are preserved — only HTML structure and CSS classes change. The Optimizer overlay is extracted to its own standalone page.

**Tech Stack:** Plain HTML5, CSS custom properties, vanilla JS. FastAPI static file serving. No build step.

**Spec:** `docs/superpowers/specs/2026-03-15-lcars-ui-overhaul-design.md`

---

## Chunk 1: Foundation — lcars.css, lcars.js, Routes

### Task 1: Create lcars.css — shared design system

**Files:**
- Create: `frontend/static/lcars.css`

- [ ] **Step 1: Create the file with CSS custom properties and all shared components**

```css
/* frontend/static/lcars.css — DocVault LCARS Design System */

/* ── Custom Properties ─────────────────────────────────── */
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
}

/* ── Reset ─────────────────────────────────────────────── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--c-bg);
  color: var(--c-label);
  font-family: var(--font);
  min-height: 100vh;
}
a { color: var(--c-accent); text-decoration: none; }
a:hover { color: var(--c-warn); }

/* ── Two-Row Navbar ────────────────────────────────────── */
.lc-nav { position: sticky; top: 0; z-index: 100; }

.lc-nav-top {
  display: flex;
  align-items: stretch;
  background: var(--c-nav);
  height: 40px;
  border-bottom: 1px solid var(--c-border-str);
}
.lc-elbow {
  width: 58px;
  flex-shrink: 0;
  background: var(--c-accent-dim);
  border-radius: 0 0 22px 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  font-weight: 900;
  color: #000;
  letter-spacing: -1px;
  text-decoration: none;
}
.lc-brand {
  display: flex;
  align-items: center;
  padding: 0 16px;
  color: var(--c-accent);
  font-size: 13px;
  font-weight: bold;
  letter-spacing: 3px;
  text-transform: uppercase;
  flex-shrink: 0;
  text-decoration: none;
}
.lc-nav-links {
  display: flex;
  align-items: center;
  gap: 3px;
  padding: 0 8px;
  flex: 1;
}
.lc-pill {
  background: #1a0d00;
  color: var(--c-accent);
  border-radius: 10px;
  padding: 3px 12px;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 1px;
  border: 1px solid #663300;
  text-decoration: none;
  white-space: nowrap;
  transition: background 0.15s;
}
.lc-pill:hover { background: #2a1500; color: var(--c-warn); }
.lc-pill.active { background: var(--c-accent-dim); color: #000; font-weight: bold; border-color: var(--c-accent-dim); }

.lc-nav-bot {
  height: 22px;
  background: #040200;
  border-bottom: 3px solid var(--c-accent);
  display: flex;
  align-items: center;
  padding: 0 14px;
  gap: 20px;
  overflow: hidden;
}
.lc-sensor {
  font-size: 11px;
  color: var(--c-label-dim);
  text-transform: uppercase;
  letter-spacing: 1px;
  white-space: nowrap;
  flex-shrink: 0;
}
.lc-sensor span       { color: var(--c-warn);  margin-left: 3px; }
.lc-sensor span.ok    { color: var(--c-ok);    }
.lc-sensor span.err   { color: var(--c-error); }
.lc-sensor span.warn  { color: var(--c-warn);  }
.lc-sensor-push       { margin-left: auto; }

/* ── Section Title ─────────────────────────────────────── */
.lc-section-title {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 20px 10px;
  color: var(--c-accent-dim);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 3px;
}
.lc-section-title::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--c-border);
}

/* ── Bar Badge ─────────────────────────────────────────── */
.lc-bar-badges { display: flex; flex-direction: column; gap: 6px; }

.lc-bar-badge {
  display: flex;
  align-items: stretch;
  height: 54px;
  border-radius: 27px 4px 4px 27px;
  overflow: hidden;
  cursor: pointer;
  border: 1px solid var(--c-border);
  text-decoration: none;
  transition: filter 0.15s;
}
.lc-bar-badge:hover { filter: brightness(1.2); }
.lc-bar-badge.active .lc-bar-body { background: #150a00; }
.lc-bar-badge.active .lc-bar-arrow { color: var(--c-accent); }

/* Accordion-open variant: flatten bottom corners */
.lc-bar-badge.open {
  border-radius: 27px 4px 0 0;
  border-bottom-color: transparent;
}

.lc-bar-tab {
  width: 54px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  border-radius: 27px 0 0 27px;
}
.lc-bar-body {
  flex: 1;
  background: var(--c-surface);
  display: flex;
  align-items: center;
  padding: 0 18px;
  gap: 16px;
  min-width: 0;
}
.lc-bar-name {
  color: var(--c-accent);
  font-size: 14px;
  font-weight: bold;
  text-transform: uppercase;
  letter-spacing: 2px;
  white-space: nowrap;
  flex-shrink: 0;
}
.lc-bar-desc {
  color: var(--c-label);
  font-size: 11px;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.lc-bar-arrow {
  color: var(--c-accent-dim);
  font-size: 16px;
  flex-shrink: 0;
}
.lc-bar-expand {
  background: var(--c-panel);
  border: 1px solid var(--c-border);
  border-top: none;
  border-radius: 0 0 4px 4px;
  padding: 14px 20px;
  display: none;
}
.lc-bar-expand.open { display: block; }

/* ── Stat Tile ─────────────────────────────────────────── */
.lc-stat-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 6px;
  padding: 0 20px 12px;
}
.lc-stat-tile {
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-left: 3px solid var(--c-accent-dim);
  padding: 10px 12px;
  cursor: pointer;
  transition: background 0.15s;
}
.lc-stat-tile:hover { background: #110a00; }
.lc-stat-val {
  font-size: 22px;
  font-weight: bold;
  color: var(--c-accent);
  line-height: 1;
}
.lc-stat-lbl {
  font-size: 10px;
  color: var(--c-label);
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-top: 3px;
}

/* ── Vault Card ────────────────────────────────────────── */
.lc-vault-cards {
  display: flex;
  gap: 8px;
  padding: 0 20px 14px;
  overflow-x: auto;
}
.lc-vault-card {
  flex-shrink: 0;
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-top: 3px solid var(--c-accent-dim);
  border-radius: 2px;
  padding: 10px 14px;
  min-width: 170px;
  max-width: 220px;
}
.lc-vault-card.archived { opacity: 0.5; border-top-color: #333; }
.lc-vault-name {
  color: var(--c-accent);
  font-size: 12px;
  font-weight: bold;
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-bottom: 3px;
}
.lc-vault-path {
  color: var(--c-label);
  font-size: 11px;
  margin-bottom: 7px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.lc-vault-meta {
  display: flex;
  gap: 12px;
  font-size: 11px;
  color: var(--c-label);
  margin-bottom: 6px;
}
.lc-vault-meta span { color: var(--c-warn); }

/* ── Status Badges ─────────────────────────────────────── */
.lc-badge {
  display: inline-block;
  border-radius: 8px;
  padding: 2px 9px;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 1px;
  white-space: nowrap;
}
.lc-badge-ok       { background: #002211; color: var(--c-ok);   border: 1px solid #005533; }
.lc-badge-proc     { background: #110033; color: var(--c-proc); border: 1px solid #330066; }
.lc-badge-err      { background: #220000; color: var(--c-error);border: 1px solid #550000; }
.lc-badge-pend     { background: #111100; color: var(--c-pend); border: 1px solid #333300; }
.lc-badge-unk      { background: #111;    color: var(--c-unk);  border: 1px solid #333; }
.lc-badge-active   { background: #112200; color: var(--c-ok);   border: 1px solid #335500; }
.lc-badge-archived { background: #1a1a1a; color: #555;          border: 1px solid #333; }

/* ── Filter Pills ──────────────────────────────────────── */
.lc-filters { display: flex; gap: 5px; padding: 0 20px 10px; flex-wrap: wrap; }
.lc-filter-pill {
  background: #110800;
  color: #dd8833;
  border-radius: 8px;
  padding: 3px 11px;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 1px;
  border: 1px solid #2a1400;
  cursor: pointer;
  transition: background 0.15s;
}
.lc-filter-pill:hover { background: #1a0d00; }
.lc-filter-pill.active { background: var(--c-accent-dim); color: #000; font-weight: bold; border-color: var(--c-accent-dim); }

/* ── Table ─────────────────────────────────────────────── */
.lc-table-wrap { padding: 0 20px; overflow-x: auto; }
.lc-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.lc-table th {
  color: var(--c-label);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 1px;
  padding: 7px 10px;
  border-bottom: 1px solid var(--c-border);
  text-align: left;
  cursor: pointer;
  white-space: nowrap;
}
.lc-table th:hover { color: var(--c-accent); }
.lc-table td {
  padding: 7px 10px;
  border-bottom: 1px solid #0d0600;
  color: #cc9966;
  vertical-align: middle;
}
.lc-table tr:hover td { background: #0d0800; }
.lc-table td.lc-filename {
  color: var(--c-accent);
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── Divider ───────────────────────────────────────────── */
.lc-divider {
  height: 3px;
  background: var(--c-border-str);
  position: relative;
  margin: 4px 0;
}
.lc-divider-label {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  top: -8px;
  background: var(--c-bg);
  padding: 0 12px;
  color: var(--c-label-dim);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 2px;
}

/* ── Button ────────────────────────────────────────────── */
.lc-btn {
  background: #1a0d00;
  color: var(--c-accent);
  border: 1px solid var(--c-accent-dim);
  border-radius: 10px;
  padding: 5px 16px;
  font-family: var(--font);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 1px;
  cursor: pointer;
  transition: background 0.15s;
}
.lc-btn:hover { background: var(--c-accent-dim); color: #000; }
.lc-btn-primary { background: var(--c-accent-dim); color: #000; font-weight: bold; }
.lc-btn-primary:hover { background: var(--c-accent); }
.lc-btn-danger { border-color: #661111; color: var(--c-error); }
.lc-btn-danger:hover { background: #330000; }
.lc-btn-sm { padding: 3px 10px; font-size: 10px; }

/* ── Input / Select ────────────────────────────────────── */
.lc-input, .lc-select {
  background: var(--c-panel);
  color: var(--c-accent);
  border: 1px solid var(--c-border-str);
  border-radius: 3px;
  padding: 6px 10px;
  font-family: var(--font);
  font-size: 12px;
  width: 100%;
}
.lc-input:focus, .lc-select:focus {
  outline: none;
  border-color: var(--c-accent-dim);
}
.lc-input::placeholder { color: var(--c-label-dim); }
.lc-select option { background: var(--c-panel); }

/* ── Toast ─────────────────────────────────────────────── */
.lc-toast {
  position: fixed;
  top: 70px;
  right: 20px;
  z-index: 9999;
  background: var(--c-surface);
  border: 1px solid var(--c-accent-dim);
  border-left: 3px solid var(--c-accent-dim);
  border-radius: 3px;
  padding: 10px 16px;
  color: var(--c-accent);
  font-size: 12px;
  max-width: 320px;
  opacity: 0;
  transform: translateX(20px);
  transition: opacity 0.2s, transform 0.2s;
  pointer-events: none;
}
.lc-toast.show { opacity: 1; transform: translateX(0); }
.lc-toast.err { border-color: var(--c-error); color: var(--c-error); }

/* ── Modal ─────────────────────────────────────────────── */
.lc-modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.8);
  z-index: 1000;
  display: none;
  align-items: center;
  justify-content: center;
}
.lc-modal-backdrop.open { display: flex; }
.lc-modal {
  background: var(--c-surface);
  border: 1px solid var(--c-border-str);
  border-top: 3px solid var(--c-accent-dim);
  border-radius: 2px;
  padding: 24px;
  min-width: 380px;
  max-width: 560px;
  width: 90%;
  max-height: 90vh;
  overflow-y: auto;
}
.lc-modal-title {
  color: var(--c-accent);
  font-size: 13px;
  font-weight: bold;
  text-transform: uppercase;
  letter-spacing: 2px;
  margin-bottom: 16px;
}

/* ── Page wrapper ──────────────────────────────────────── */
.lc-page { background: var(--c-bg); min-height: calc(100vh - 65px); }
.lc-page-pad { padding: 0 0 60px; }

/* ── Context Menu ──────────────────────────────────────── */
.lc-ctx-menu {
  position: fixed;
  z-index: 5000;
  background: var(--c-surface);
  border: 1px solid var(--c-border-str);
  border-radius: 3px;
  min-width: 160px;
  display: none;
  box-shadow: 0 4px 20px rgba(0,0,0,0.6);
}
.lc-ctx-menu.open { display: block; }
.lc-ctx-item {
  padding: 8px 14px;
  font-size: 11px;
  color: var(--c-label);
  cursor: pointer;
  text-transform: uppercase;
  letter-spacing: 1px;
  border-bottom: 1px solid var(--c-border);
}
.lc-ctx-item:last-child { border-bottom: none; }
.lc-ctx-item:hover { background: #110a00; color: var(--c-accent); }
.lc-ctx-sep { height: 1px; background: var(--c-border-str); margin: 2px 0; }

/* ── Scrollbar ─────────────────────────────────────────── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: var(--c-nav); }
::-webkit-scrollbar-thumb { background: var(--c-accent-dim); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--c-accent); }
```

- [ ] **Step 2: Verify the file was created**

```bash
wc -l frontend/static/lcars.css
```
Expected: ~280+ lines

- [ ] **Step 3: Commit**

```bash
git add frontend/static/lcars.css
git commit -m "feat(ui): add lcars.css — shared LCARS design system"
```

---

### Task 2: Create lcars.js — shared navbar + sensor rail

**Files:**
- Create: `frontend/static/lcars.js`

- [ ] **Step 1: Create the file**

```javascript
/* frontend/static/lcars.js — Shared navbar + sensor rail for all DocVault pages */

/**
 * NAVBAR HTML injected into every .lc-nav container.
 * data-page attribute on <body> sets the active pill.
 * Sub-pages: lab, telemetry, optimizer → 'utilities' parent active.
 */
const NAV_HTML = `
<div class="lc-nav-top">
  <a href="/" class="lc-elbow">DV</a>
  <a href="/" class="lc-brand">DocVault</a>
  <nav class="lc-nav-links">
    <a href="/search"   class="lc-pill" data-nav="search">Search</a>
    <a href="/vault"    class="lc-pill" data-nav="vault">Vault</a>
    <a href="/identity" class="lc-pill" data-nav="identity">Identity</a>
    <a href="/utils"    class="lc-pill" data-nav="utilities">Utilities</a>
    <a href="/settings" class="lc-pill" data-nav="settings">Settings</a>
  </nav>
</div>
<div class="lc-nav-bot" id="lc-sensor-rail">
  <span class="lc-sensor" id="lc-s-workers">Workers <span>—</span></span>
  <span class="lc-sensor" id="lc-s-cpu">CPU <span>—</span></span>
  <span class="lc-sensor" id="lc-s-gpu">GPU <span>—</span></span>
  <span class="lc-sensor" id="lc-s-temp">Temp <span>—</span></span>
  <span class="lc-sensor" id="lc-s-disk">Disk <span>—</span></span>
  <span class="lc-sensor lc-sensor-push" id="lc-s-state">State <span>—</span></span>
</div>`;

/** Pages that live under Utilities in the nav hierarchy */
const UTILITIES_SUBPAGES = ['lab', 'telemetry', 'optimizer'];

function lcInit() {
  // 1. Inject navbar
  const navEl = document.querySelector('.lc-nav');
  if (navEl) {
    navEl.innerHTML = NAV_HTML;

    // 2. Set active pill
    const page = document.body.dataset.page || '';
    const activeKey = UTILITIES_SUBPAGES.includes(page) ? 'utilities' : page;
    const pill = navEl.querySelector(`[data-nav="${activeKey}"]`);
    if (pill) pill.classList.add('active');
  }

  // 3. Start sensor rail polling
  lcPollSensors();
  setInterval(lcPollSensors, 5000);

  // 4. Init shared toast
  if (!document.getElementById('lc-toast')) {
    const t = document.createElement('div');
    t.id = 'lc-toast';
    t.className = 'lc-toast';
    document.body.appendChild(t);
  }
}

async function lcPollSensors() {
  try {
    const [mon, wrk] = await Promise.all([
      fetch('/api/monitor/status').then(r => r.ok ? r.json() : null).catch(() => null),
      fetch('/api/workers/status').then(r => r.ok ? r.json() : null).catch(() => null),
    ]);

    if (mon) {
      // Field names from monitor.py MonitorReading:
      //   cpu_pct, gpu_util_pct, gpu_temp (not gpu_temp_c), disk_free_pct (not disk_pct), state (not throttle_state)
      _setSensor('lc-s-cpu',  `${mon.cpu_pct ?? '—'}%`, _cpuClass(mon.cpu_pct));
      _setSensor('lc-s-gpu',  `${mon.gpu_util_pct ?? '—'}%`, '');
      _setSensor('lc-s-temp', `${mon.gpu_temp ?? '—'}°C`, _tempClass(mon.gpu_temp));
      _setSensor('lc-s-disk', `${mon.disk_free_pct ?? '—'}%`, _diskClass(mon.disk_free_pct));
      const state = mon.state ?? 'UNKNOWN';
      _setSensor('lc-s-state', `▐ ${state}`, state === 'READY' ? 'ok' : state === 'PAUSED' ? 'err' : 'warn');
    }
    if (wrk) {
      // /api/workers/status returns {paused: bool} — no active_workers field
      const paused = wrk.paused ?? false;
      _setSensor('lc-s-workers', paused ? '⏸ PAUSED' : '▶ RUNNING', paused ? 'warn' : 'ok');
    }
  } catch (_) { /* never let sensor polling crash the page */ }
}

function _setSensor(id, val, cls) {
  const el = document.getElementById(id);
  if (!el) return;
  const span = el.querySelector('span');
  if (!span) return;
  span.textContent = val;
  span.className = cls || '';
}
function _cpuClass(v)  { return v > 90 ? 'err' : v > 70 ? 'warn' : 'ok'; }
function _tempClass(v) { return v > 85 ? 'err' : v > 70 ? 'warn' : 'ok'; }
function _diskClass(v) { return v > 90 ? 'err' : v > 75 ? 'warn' : 'ok'; }

/** Shared toast helper — replaces per-page showToast() */
function lcToast(msg, isErr = false) {
  const t = document.getElementById('lc-toast');
  if (!t) return;
  t.textContent = msg;
  t.className = 'lc-toast' + (isErr ? ' err' : '');
  // force reflow
  void t.offsetWidth;
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), 2800);
}

/** Accordion toggle for bar badges used in settings + utilities */
function lcAccordion(badge) {
  const expand = badge.nextElementSibling;
  if (!expand || !expand.classList.contains('lc-bar-expand')) return;
  const isOpen = expand.classList.contains('open');
  // close all siblings first
  const parent = badge.parentElement;
  parent.querySelectorAll('.lc-bar-badge.open').forEach(b => {
    b.classList.remove('open');
    const e = b.nextElementSibling;
    if (e) e.classList.remove('open');
    const arr = b.querySelector('.lc-bar-arrow');
    if (arr) arr.textContent = '▶';
  });
  if (!isOpen) {
    badge.classList.add('open');
    expand.classList.add('open');
    const arr = badge.querySelector('.lc-bar-arrow');
    if (arr) arr.textContent = '▼';
  }
}

document.addEventListener('DOMContentLoaded', lcInit);
```

- [ ] **Step 2: Verify**

```bash
wc -l frontend/static/lcars.js
```
Expected: ~110+ lines

- [ ] **Step 3: Commit**

```bash
git add frontend/static/lcars.js
git commit -m "feat(ui): add lcars.js — shared navbar injection and sensor rail polling"
```

---

### Task 3: Update api/main.py routes

**Files:**
- Modify: `api/main.py`

- [ ] **Step 1: Read the current route definitions in api/main.py**

Find the section with `FileResponse` calls for HTML pages (search for `FileResponse`).

- [ ] **Step 2: Update routes**

First ensure these imports are present at the top of `api/main.py`:
```python
from pathlib import Path
from fastapi.responses import RedirectResponse, FileResponse
```

Replace the block of HTML-serving routes with:

```python
# ── HTML page routes ────────────────────────────────────
FRONTEND = Path("frontend")

@app.get("/",          include_in_schema=False)
@app.get("/status",    include_in_schema=False)
@app.get("/vault",     include_in_schema=False)
async def vault_page():
    return FileResponse(FRONTEND / "vault.html")

@app.get("/catalog",   include_in_schema=False)
async def catalog_redirect():
    return RedirectResponse(url="/vault", status_code=301)

@app.get("/search",    include_in_schema=False)
async def search_page():
    return FileResponse(FRONTEND / "search.html")

@app.get("/utils",     include_in_schema=False)
async def utils_page():
    return FileResponse(FRONTEND / "utils.html")

@app.get("/optimizer", include_in_schema=False)
async def optimizer_page():
    return FileResponse(FRONTEND / "optimizer.html")

@app.get("/settings",  include_in_schema=False)
async def settings_page():
    return FileResponse(FRONTEND / "settings.html")

@app.get("/lab",       include_in_schema=False)
@app.get("/lab.html",  include_in_schema=False)
async def lab_page():
    return FileResponse(FRONTEND / "lab.html")

@app.get("/telemetry",      include_in_schema=False)
@app.get("/telemetry.html", include_in_schema=False)
async def telemetry_page():
    return FileResponse(FRONTEND / "telemetry.html")

@app.get("/identity",  include_in_schema=False)
async def identity_page():
    return FileResponse(FRONTEND / "identity.html")
```

- [ ] **Step 3: Create a temporary vault.html placeholder so routes work immediately**

```bash
cp frontend/index.html frontend/vault.html
```

- [ ] **Step 4: Restart server and verify routes**

```
GET http://localhost:8000/        → 200 (vault.html placeholder)
GET http://localhost:8000/vault   → 200
GET http://localhost:8000/catalog → 301 → /vault
GET http://localhost:8000/optimizer → 200 (will be 404 until optimizer.html created — that's fine)
```

- [ ] **Step 5: Commit**

```bash
git add api/main.py frontend/vault.html
git commit -m "feat(routes): add /vault, /optimizer routes; 301 /catalog→/vault"
```

---

## Chunk 2: vault.html — Merged Vault Status + Log

### Task 4: Create vault.html — structure, stat grid, vault cards

**Files:**
- Create: `frontend/vault.html` (replaces placeholder + index.html)

This task builds the complete vault.html. It preserves ALL JavaScript from index.html and catalog.html, restructured around the LCARS layout.

- [ ] **Step 1: Read index.html and catalog.html in full before writing**

Read both files completely to understand every JS function, API call, and modal before replacing anything.

- [ ] **Step 2: Write vault.html**

The file structure:
```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>DocVault — Vault</title>
  <link rel="stylesheet" href="/static/lcars.css">
  <!-- NO Tailwind CDN -->
  <style>
    /* page-specific overrides only */
    .vault-worker-controls { display:flex; gap:6px; margin-left:auto; padding-right:20px; }
    .unknown-row { display:flex; flex-wrap:wrap; gap:6px; padding:0 20px 12px; }
    .unknown-chip {
      background:#0d0800; border:1px solid var(--c-border-str);
      border-left:3px solid var(--c-unk);
      padding:4px 10px; font-size:11px; color:var(--c-unk);
      cursor:pointer;
    }
    .unknown-chip:hover { color:var(--c-warn); }
  </style>
</head>
<body data-page="vault">
  <div class="lc-nav"></div>  <!-- lcars.js injects navbar here -->

  <div class="lc-page lc-page-pad">

    <!-- ── TOP: Vault Status ───────────────────── -->
    <div class="lc-section-title" style="padding-top:14px;">
      Vault Status
      <div class="vault-worker-controls">
        <button class="lc-btn lc-btn-sm" onclick="pauseWorkers()">Pause</button>
        <button class="lc-btn lc-btn-sm" onclick="resumeWorkers()">Resume</button>
        <button class="lc-btn lc-btn-sm lc-btn-danger" onclick="resetStuck()">Reset Stuck</button>
      </div>
    </div>

    <!-- Stat grid (6 tiles, color-coded) -->
    <div class="lc-stat-grid" id="stat-grid">
      <!-- populated by loadStats() -->
    </div>

    <!-- Vault cards -->
    <div class="lc-vault-cards" id="vault-cards">
      <!-- populated by loadVaultCards() -->
      <div style="color:var(--c-label-dim);font-size:11px;padding:10px;">Loading vaults…</div>
    </div>

    <!-- Unknown file types strip -->
    <div id="unknowns-section" style="display:none;">
      <div class="lc-section-title" style="font-size:10px;">Unknown File Types</div>
      <div class="unknown-row" id="unknowns-row"></div>
    </div>

    <!-- ── DIVIDER ─────────────────────────────── -->
    <div class="lc-divider"><div class="lc-divider-label">Vault Log</div></div>

    <!-- ── BOTTOM: Vault Log ──────────────────── -->
    <div id="vault-log-section">
      <div class="lc-section-title">Recent Files</div>

      <div class="lc-filters" id="filter-bar">
        <div class="lc-filter-pill active" data-filter="ALL" onclick="setFilter('ALL',this)">All</div>
        <div class="lc-filter-pill" data-filter="COMPLETED"  onclick="setFilter('COMPLETED',this)">Completed</div>
        <div class="lc-filter-pill" data-filter="PROCESSING" onclick="setFilter('PROCESSING',this)">Processing</div>
        <div class="lc-filter-pill" data-filter="PENDING"    onclick="setFilter('PENDING',this)">Pending</div>
        <div class="lc-filter-pill" data-filter="ERROR"      onclick="setFilter('ERROR',this)">Error</div>
        <div class="lc-filter-pill" data-filter="UNKNOWN"    onclick="setFilter('UNKNOWN',this)">Unknown</div>
        <!-- vault filter pills appended by loadVaultFilter() -->
      </div>

      <div class="lc-table-wrap">
        <table class="lc-table" id="catalog-table">
          <thead>
            <tr>
              <th onclick="sortBy('file_path')">File <span id="sort-file_path"></span></th>
              <th onclick="sortBy('file_type')">Type <span id="sort-file_type"></span></th>
              <th onclick="sortBy('vault_id')">Vault <span id="sort-vault_id"></span></th>
              <th onclick="sortBy('status')">Status <span id="sort-status"></span></th>
              <th onclick="sortBy('file_size')">Size <span id="sort-file_size"></span></th>
              <th onclick="sortBy('last_update')">Updated <span id="sort-last_update"></span></th>
              <th></th>
            </tr>
          </thead>
          <tbody id="catalog-body">
            <tr><td colspan="7" style="color:var(--c-label-dim);text-align:center;padding:20px;">Loading…</td></tr>
          </tbody>
        </table>
      </div>

      <!-- Pagination -->
      <div id="pagination" style="display:flex;gap:8px;padding:12px 20px;align-items:center;">
        <button class="lc-btn lc-btn-sm" id="btn-prev" onclick="prevPage()">◀ Prev</button>
        <span id="page-info" style="font-size:11px;color:var(--c-label);">Page 1</span>
        <button class="lc-btn lc-btn-sm" id="btn-next" onclick="nextPage()">Next ▶</button>
      </div>
    </div>

  </div><!-- /lc-page -->

  <!-- Modals: copy verbatim from source files, replacing only CSS classes -->
  <!--
    VAULT MODAL: copy the entire #reset-modal div from index.html.
    - Replace class="fixed inset-0 ..." with class="lc-modal-backdrop"
    - Replace the inner white panel div with class="lc-modal"
    - Replace modal title div with class="lc-modal-title"
    - Replace all Tailwind input/button/label classes with lc-input / lc-btn / lc-label
    - Preserve all id="" attributes and all onclick/onchange handlers exactly

    DETAIL OVERLAY: copy the entire #detail-overlay and #detail divs from catalog.html verbatim.
    Replace only Tailwind background/border/text classes with LCARS equivalents.
    Preserve all JS that populates #detail innerHTML.
  -->

  <!-- detail-overlay: catalog item detail (preserve from catalog.html) -->
  <div id="detail-overlay" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.8);z-index:2000;overflow-y:auto;">
    <div id="detail" style="max-width:680px;margin:40px auto;background:var(--c-surface);border:1px solid var(--c-border-str);border-top:3px solid var(--c-accent-dim);padding:24px;">
      <!-- populated by showDetail() -->
    </div>
  </div>

  <!-- ctx-menu -->
  <div id="ctx-menu" class="lc-ctx-menu">
    <div class="lc-ctx-item" onclick="ctxOpenFile()">Open File</div>
    <div class="lc-ctx-sep"></div>
    <div class="lc-ctx-item" onclick="ctxOpenFolder()">Open Folder</div>
    <div class="lc-ctx-sep"></div>
    <div class="lc-ctx-item" onclick="ctxInspect()">Inspect</div>
    <div class="lc-ctx-sep"></div>
    <div class="lc-ctx-item" onclick="ctxReprocess()">Reprocess</div>
  </div>

  <!-- stuck-banner -->
  <div id="stuck-banner" style="display:none;background:#220000;border-top:2px solid var(--c-error);padding:8px 20px;font-size:11px;color:var(--c-error);">
    ⚠ <strong id="stuck-count"></strong> stuck tasks detected.
    <button class="lc-btn lc-btn-sm lc-btn-danger" onclick="resetStuck()" style="margin-left:10px;">Reset</button>
  </div>

  <script src="/static/app.js"></script>
  <script src="/static/lcars.js"></script>
  <script>
    // ── Preserve ALL JS from index.html and catalog.html ──────────────
    // Merge both scripts here. Key functions needed:
    //
    // FROM index.html:
    //   loadStats(), loadVaultCards(), loadWorkerStatus(), loadUnknowns()
    //   pauseWorkers(), resumeWorkers(), resetStuck()
    //   openVaultModal(), saveVault(), deleteVault()
    //
    // FROM catalog.html:
    //   loadCatalog(), sortBy(), setFilter(), prevPage(), nextPage()
    //   showDetail(), loadVaultFilter()
    //   ctxOpenFile(), ctxOpenFolder(), ctxInspect(), ctxReprocess()
    //
    // CHANGES from originals:
    //   - Status tile clicks call: setFilter(status); scrollToLog()
    //   - URL param handling: ?status=X → setFilter(X); ?query=X → set query filter
    //   - showToast() → lcToast()
    //   - Tailwind class references in dynamic HTML → lc-* classes
    //   - Status badge HTML: use lc-badge + lc-badge-{ok/err/proc/pend/unk}
    //   - Vault card HTML: use lc-vault-card (+ .archived if status=archived)
    //
    // Copy the full JS bodies from both source files, applying the above changes.

    const PAGE_SIZE = 50;
    let _page = 0, _sortBy = 'last_update', _sortDir = 'desc', _filter = 'ALL', _vaultFilter = '';

    function scrollToLog() {
      document.getElementById('vault-log-section').scrollIntoView({behavior:'smooth'});
    }

    // Status→badge class map
    const STATUS_CLASS = {
      COMPLETED:'ok', EXTRACTED:'ok', PROCESSING:'proc', EMBEDDING:'proc',
      PENDING:'pend', ERROR:'err', UNKNOWN:'unk'
    };
    function statusBadge(s) {
      const cls = STATUS_CLASS[s] || 'unk';
      return `<span class="lc-badge lc-badge-${cls}">${s}</span>`;
    }

    // Handle URL params on load
    window.addEventListener('DOMContentLoaded', () => {
      const p = new URLSearchParams(location.search);
      if (p.get('status')) setFilter(p.get('status'), null);
      if (p.get('query'))  { /* set query input value and trigger filter */ }
    });

    // START: polling intervals (preserve exact timing from originals)
    // loadStats()     → 5s
    // loadVaultCards() → 30s
    // loadCatalog()   → on filter/sort change only (no auto-poll)
    // loadUnknowns()  → on load
  </script>
</body>
</html>
```

**Important:** The `<script>` block must contain the complete merged JS from both `index.html` and `catalog.html`. Do not leave stub comments — copy the actual function bodies, updating only the class names and `showToast` → `lcToast` calls.

- [ ] **Step 3: Open http://localhost:8000/vault in browser and verify**

- Navbar shows: Search · Vault (active) · Identity · Utilities · Settings
- Sensor rail shows live values
- Stat grid renders 6 tiles with correct colors
- Vault cards render correctly, archived cards are dimmed
- Log table loads and is sortable
- Filter pills work
- Right-click context menu works
- Stat tile click → scrolls to log + filters

- [ ] **Step 4: Verify /catalog redirect**

```
GET http://localhost:8000/catalog  → 301 → /vault  (check browser address bar)
```

- [ ] **Step 5: Commit**

```bash
git add frontend/vault.html
git commit -m "feat(ui): add vault.html — merged vault status and log with LCARS design"
```

- [ ] **Step 6: Scan for any remaining references to index.html or catalog.html**

```bash
grep -r "catalog\|index\.html" frontend/ --include="*.html" -l
```

**Do NOT remove index.html or catalog.html yet** — identity.html still links to `/catalog?query=...` (fixed in Task 8). Defer deletion to Task 11.

---

## Chunk 3: Search + Utilities + Optimizer

### Task 5: Restyle search.html — bar badge mode tabs

**Files:**
- Modify: `frontend/search.html`

- [ ] **Step 1: Read search.html in full**

- [ ] **Step 2: Replace Tailwind with LCARS — key structural changes**

Replace the three tab buttons (Content / Filename / Ask) with bar badges:

```html
<!-- Replace existing tab buttons with: -->
<div style="padding:14px 20px 0;">
  <div class="lc-bar-badges" id="search-mode-badges">
    <div class="lc-bar-badge active" id="tab-content"
         onclick="switchMode('content')" data-mode="content">
      <div class="lc-bar-tab" style="background:#cc6600;">🔍</div>
      <div class="lc-bar-body">
        <div class="lc-bar-name">Content Search</div>
        <div class="lc-bar-desc">Hybrid FTS + semantic search across extracted text</div>
        <div class="lc-bar-arrow">▶</div>
      </div>
    </div>
    <div class="lc-bar-badge" id="tab-filename"
         onclick="switchMode('filename')" data-mode="filename">
      <div class="lc-bar-tab" style="background:#886600;">📁</div>
      <div class="lc-bar-body">
        <div class="lc-bar-name">Filename Search</div>
        <div class="lc-bar-desc">Find files by name pattern across all vaults</div>
        <div class="lc-bar-arrow">▶</div>
      </div>
    </div>
    <div class="lc-bar-badge" id="tab-ask"
         onclick="switchMode('ask')" data-mode="ask">
      <div class="lc-bar-tab" style="background:#664400;">💬</div>
      <div class="lc-bar-body">
        <div class="lc-bar-name">Ask</div>
        <div class="lc-bar-desc">RAG — ask questions answered from your documents</div>
        <div class="lc-bar-arrow">▶</div>
      </div>
    </div>
  </div>
</div>

<!-- Mode panels (one shown at a time, same content as existing tabs) -->
<div id="panel-content" style="padding:14px 20px;">...</div>
<div id="panel-filename" style="display:none;padding:14px 20px;">...</div>
<div id="panel-ask" style="display:none;padding:14px 20px;">...</div>
```

Mode switch JS:
```javascript
function switchMode(mode) {
  ['content','filename','ask'].forEach(m => {
    document.getElementById('tab-'+m).classList.toggle('active', m===mode);
    document.getElementById('panel-'+m).style.display = m===mode ? 'block' : 'none';
  });
}
```

- Replace `<script src="https://cdn.tailwindcss.com">` with `<link rel="stylesheet" href="/static/lcars.css">`
- Add `<script src="/static/lcars.js"></script>` before closing `</body>`
- Add `data-page="search"` to `<body>`
- Replace the existing `<nav>` block with `<div class="lc-nav"></div>`
- Replace `showToast()` calls with `lcToast()`
- Restyle result cards, vault selector, input fields, and degraded-service banner using LCARS classes
- Amber degraded banner: `background:#221100; border:1px solid var(--c-warn); color:var(--c-warn);`

- [ ] **Step 3: Verify in browser (http://localhost:8000/search)**

- Three bar badges visible, Content Search active by default
- Clicking each badge shows its panel, hides others
- Search returns results styled in LCARS
- Vault selector checkboxes visible
- Ask tab shows chat interface

- [ ] **Step 4: Commit**

```bash
git add frontend/search.html
git commit -m "feat(ui): restyle search.html with LCARS bar badge mode tabs"
```

---

### Task 6: Extract optimizer.html from utils.html overlay

**Files:**
- Create: `frontend/optimizer.html`
- Modify: `frontend/utils.html` (remove optimizer overlay)

- [ ] **Step 1: Read utils.html in full — note every mc* function and DOM ID**

- [ ] **Step 2: Create optimizer.html as a full page**

Structure:
```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>DocVault — Optimizer</title>
  <link rel="stylesheet" href="/static/lcars.css">
  <style>
    /* Preserve the Mission Control grid layout from the overlay */
    /* Q1..Q4 grid, log panel, parameter panel, profile cards */
    /* Replace #mc-overlay positioning (fixed inset-0) with normal flow */
    .mc-grid { display:grid; grid-template-columns:1fr 1fr; grid-template-rows:auto auto; gap:10px; padding:14px 20px; }
    .mc-panel { background:var(--c-surface); border:1px solid var(--c-border); padding:14px; }
    .mc-panel-title { color:var(--c-accent-dim); font-size:10px; text-transform:uppercase; letter-spacing:2px; margin-bottom:10px; }
    /* ... preserve all other mc- styles from utils.html, updated to LCARS palette ... */
  </style>
</head>
<body data-page="optimizer">
  <div class="lc-nav"></div>

  <div class="lc-page lc-page-pad">
    <div class="lc-section-title">Mission Control — Optimizer</div>

    <!-- Controls bar -->
    <div style="display:flex;gap:8px;padding:0 20px 12px;align-items:center;">
      <select class="lc-select" id="mc-vault-sel" style="width:200px;"></select>
      <button class="lc-btn lc-btn-primary" id="mc-start-btn" onclick="mcStartRun()">▶ Run</button>
      <button class="lc-btn lc-btn-danger" id="mc-abort-btn" onclick="mcAbort()" style="display:none;">■ Abort</button>
      <span id="mc-run-label" style="font-size:11px;color:var(--c-label);"></span>
      <span id="mc-eta" style="font-size:11px;color:var(--c-warn);margin-left:auto;"></span>
    </div>

    <!-- Q1-Q4 telemetry grid (preserve exact layout from overlay) -->
    <div class="mc-grid">
      <!-- Q1: CPU/RAM/GPU gauges -->
      <!-- Q2: Parameter config panel -->
      <!-- Q3: Throughput + headroom meters -->
      <!-- Q4: System log -->
    </div>

    <!-- Profile cards (shown after run completes) -->
    <div id="mc-profile-cards" style="padding:0 20px;"></div>
  </div>

  <script src="/static/app.js"></script>
  <script src="/static/lcars.js"></script>
  <script>
    // Copy ALL mc* functions from utils.html exactly.
    // Remove: openOptimizer(), closeOptimizer() (no longer needed — it's a page)
    // On DOMContentLoaded: call mcLoadVaults(), mcCheckLastConfig(), mcBuildConfigPanel()
    // mcLog() target: #mc-log (same ID, now in normal flow not overlay)
  </script>
</body>
</html>
```

- [ ] **Step 3: Restyle utils.html as hub — remove overlay, add bar badges**

Replace entire contents with:
```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>DocVault — Utilities</title>
  <link rel="stylesheet" href="/static/lcars.css">
</head>
<body data-page="utilities">
  <div class="lc-nav"></div>
  <div class="lc-page lc-page-pad">
    <div class="lc-section-title">Utilities</div>
    <div style="padding:0 20px;">
      <div class="lc-bar-badges">

        <!-- Full-page nav badges -->
        <a href="/lab" class="lc-bar-badge">
          <div class="lc-bar-tab" style="background:#cc6600;">⚗</div>
          <div class="lc-bar-body">
            <div class="lc-bar-name">Extractor Lab</div>
            <div class="lc-bar-desc">Test kernels, certify extractors, simulate pipelines</div>
            <div class="lc-bar-arrow">▶</div>
          </div>
        </a>

        <a href="/telemetry" class="lc-bar-badge">
          <div class="lc-bar-tab" style="background:#886600;">📡</div>
          <div class="lc-bar-body">
            <div class="lc-bar-name">Telemetry</div>
            <div class="lc-bar-desc">Live hardware gauges, worker timings, system health</div>
            <div class="lc-bar-arrow">▶</div>
          </div>
        </a>

        <a href="/optimizer" class="lc-bar-badge">
          <div class="lc-bar-tab" style="background:#664400;">⚙</div>
          <div class="lc-bar-body">
            <div class="lc-bar-name">Optimizer</div>
            <div class="lc-bar-desc">Tune extraction parameters for maximum throughput</div>
            <div class="lc-bar-arrow">▶</div>
          </div>
        </a>

        <!-- Inline accordion badges -->
        <div class="lc-bar-badge" onclick="lcAccordion(this)">
          <div class="lc-bar-tab" style="background:#443300;">🩺</div>
          <div class="lc-bar-body">
            <div class="lc-bar-name">Health Check</div>
            <div class="lc-bar-desc" id="health-status-desc">Verify Ollama, Tesseract, Poppler connectivity</div>
            <div class="lc-bar-arrow">▶</div>
          </div>
        </div>
        <div class="lc-bar-expand" id="health-expand">
          <button class="lc-btn lc-btn-sm" onclick="runHealthCheck()">Run Check</button>
          <div id="health-results" style="margin-top:10px;font-size:11px;"></div>
        </div>

        <div class="lc-bar-badge" onclick="lcAccordion(this)">
          <div class="lc-bar-tab" style="background:#334400;">☁</div>
          <div class="lc-bar-body">
            <div class="lc-bar-name">Google Drive</div>
            <div class="lc-bar-desc" id="gdrive-status-desc">Loading…</div>
            <div class="lc-bar-arrow">▶</div>
          </div>
        </div>
        <div class="lc-bar-expand" id="gdrive-expand">
          <div id="gdrive-content" style="font-size:11px;"></div>
        </div>

        <div class="lc-bar-badge" onclick="lcAccordion(this)">
          <div class="lc-bar-tab" style="background:#2a3300;">⬡</div>
          <div class="lc-bar-body">
            <div class="lc-bar-name">Qdrant</div>
            <div class="lc-bar-desc">Vector store health and collection info</div>
            <div class="lc-bar-arrow">▶</div>
          </div>
        </div>
        <div class="lc-bar-expand" id="qdrant-expand">
          <button class="lc-btn lc-btn-sm" onclick="runCheck()">Check Qdrant</button>
          <div id="qdrant-results" style="margin-top:10px;font-size:11px;"></div>
        </div>

      </div>
    </div>
  </div>

  <script src="/static/app.js"></script>
  <script src="/static/lcars.js"></script>
  <script>
    // Keep only: loadGDriveStatus(), runHealthCheck(), runCheck()
    // Remove all mc* functions (moved to optimizer.html)
    // On load: loadGDriveStatus()
  </script>
</body>
</html>
```

- [ ] **Step 4: Verify in browser**

- `http://localhost:8000/utils` — 5 bar badges, top 3 navigate to pages, bottom 3 accordion expand
- `http://localhost:8000/optimizer` — full page with Mission Control grid, navbar shows Utilities active
- Health Check expand → Run Check → shows results inline

- [ ] **Step 5: Commit**

```bash
git add frontend/utils.html frontend/optimizer.html
git commit -m "feat(ui): extract optimizer to full page; restyle utils as LCARS hub"
```

---

## Chunk 4: Settings

### Task 7: Restyle settings.html — accordion bar badges

**Files:**
- Modify: `frontend/settings.html`

- [ ] **Step 1: Read settings.html in full**

Pay attention to: `loadSettings()`, `saveSetting(key)`, how the GROUPS array drives rendering, the `pane-{id}` content containers, the description popup logic, and the reindex modal.

- [ ] **Step 2: Replace sidebar + content layout with bar badge accordion**

The current layout is a 2-column `sidebar (192px) + content` grid. Replace with a single-column bar badge list where clicking a badge opens its settings group inline.

Key changes:
- Remove the sidebar tab buttons and 2-column layout
- Each GROUPS entry becomes a `.lc-bar-badge` + `.lc-bar-expand` pair
- The `lc-bar-tab` color cycles through amber shades (use a `GROUP_COLORS` map)
- The expand panel contains the same settings fields as the current `pane-{id}` divs
- `lcAccordion()` handles open/close (already in lcars.js)
- The group count (e.g. "7 settings") shows in `.lc-bar-desc`
- `loadSettings()` populates values into inputs regardless of which pane is open
- `saveSetting()` unchanged (same API call)

Each setting field inside an expand panel renders as:
```html
<div style="margin-bottom:14px;padding-bottom:14px;border-bottom:1px solid var(--c-border);">
  <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:5px;">
    <label style="font-size:11px;color:var(--c-label);text-transform:uppercase;letter-spacing:1px;flex:1;">
      Setting Label
    </label>
    <button class="lc-btn lc-btn-sm" onclick="saveSetting('setting:key')">Save</button>
  </div>
  <input class="lc-input" id="s-setting-key" value="">
  <div style="font-size:10px;color:var(--c-label-dim);margin-top:5px;line-height:1.5;">
    Description text shown here.
  </div>
</div>
```
The last field in a group omits the bottom border. Toggle/checkbox settings use a `<select class="lc-select">` with true/false options instead of an `<input>`.

Group color map:
```javascript
const GROUP_COLORS = {
  general:'#cc6600', ollama:'#aa5500', qdrant:'#994400', pdf:'#885500',
  embeddings:'#776600', search:'#665500', vision:'#664400', video:'#554400',
  google:'#443300', bing:'#332200', huggingface:'#442200', art:'#553300',
  monitor:'#333300', lab:'#334400'
};
```

- [ ] **Step 3: Verify in browser (http://localhost:8000/settings)**

- 14 bar badges visible, all collapsed by default
- Clicking a badge opens its settings group
- Clicking another badge closes the previous and opens the new one
- Settings load correctly into inputs
- Save button works (POST to `/api/settings`)
- Reindex modal still works
- Description popup still works

- [ ] **Step 4: Commit**

```bash
git add frontend/settings.html
git commit -m "feat(ui): restyle settings.html with LCARS accordion bar badges"
```

---

## Chunk 5: Identity, Lab, Telemetry

### Task 8: Restyle identity.html

**Files:**
- Modify: `frontend/identity.html`

- [ ] **Step 1: Read identity.html in full**

- [ ] **Step 2: Apply LCARS skin**

- Replace Tailwind CDN with `lcars.css` + `lcars.js`
- Add `data-page="identity"` to `<body>`
- Replace `<nav>` with `<div class="lc-nav"></div>`
- Card grid: dark background, amber top border per card, name in `#ff9900`
- Sighting count overlay: amber text on dark semi-transparent bg
- Rename modal: use `.lc-modal-backdrop` + `.lc-modal` classes
- "Find Photos" links: update href from `/catalog?query=...` to `/vault?query=...`
- `showToast()` → `lcToast()`

- [ ] **Step 3: Verify in browser (http://localhost:8000/identity)**

- Face cards render with LCARS styling
- Rename modal opens and works
- "Find Photos" link navigates to /vault with query param

- [ ] **Step 4: Commit**

```bash
git add frontend/identity.html
git commit -m "feat(ui): restyle identity.html with LCARS skin"
```

---

### Task 9: Restyle lab.html — full page with navbar

**Files:**
- Modify: `frontend/lab.html`

- [ ] **Step 1: Read lab.html in full**

Lab already has a custom dark theme with emerald/amber/ruby colors. The main changes are:
1. Add the two-row navbar
2. Harmonize the color palette to Warm LCARS (amber/orange instead of mixed emerald/ruby)
3. Remove any overlay chrome (lab has no overlay structure — it's already full-page)

- [ ] **Step 2: Apply changes**

- Add `<link rel="stylesheet" href="/static/lcars.css">` (AFTER existing lab styles so LCARS provides the navbar, lab styles handle everything else)
- Add `<script src="/static/lcars.js"></script>`
- Add `data-page="lab"` to `<body>`
- Replace existing `<nav>` / header with `<div class="lc-nav"></div>`
- Update terminal/console colors: emerald (`#00ff88`, `#10b981`) → `#33ee77`; ruby (`#ef4444`) → `#ff4444`; slate tones → use `--c-label` and `--c-label-dim`
- The binary wizard modal: apply `.lc-modal-backdrop` + `.lc-modal` structure
- `showToast()` → `lcToast()`

- [ ] **Step 3: Verify in browser (http://localhost:8000/lab)**

- Navbar renders, Utilities pill is active
- Lab sidebar kernel list loads
- Certification flow works
- Pipeline simulator works
- Binary wizard modal opens

- [ ] **Step 4: Commit**

```bash
git add frontend/lab.html
git commit -m "feat(ui): add LCARS navbar to lab.html; harmonize color palette"
```

---

### Task 10: Restyle telemetry.html — full page with navbar

**Files:**
- Modify: `frontend/telemetry.html`

- [ ] **Step 1: Read telemetry.html in full**

Telemetry already has a dark custom theme. Changes:
1. Add two-row navbar
2. Harmonize colors to Warm LCARS
3. Remove fullscreen-only chrome (it's now always a full-width page)
4. Note: the sensor rail in the navbar will duplicate some telemetry gauges — that's intentional (nav = glanceable summary, telemetry = full detail)

- [ ] **Step 2: Apply changes**

- Add `<link rel="stylesheet" href="/static/lcars.css">` after existing telemetry styles
- Add `<script src="/static/lcars.js"></script>`
- Add `data-page="telemetry"` to `<body>`
- Replace existing `<nav>` with `<div class="lc-nav"></div>`
- Update gauge/histogram colors: emerald → `#33ee77`, amber already matches, ruby → `#ff4444`
- Remove or repurpose the fullscreen button (can keep it for browser fullscreen API)
- Stuck tasks modal: apply `.lc-modal-backdrop` + `.lc-modal`
- Tab switcher (Hardware / Performance): replace with two `.lc-filter-pill` buttons

- [ ] **Step 3: Verify in browser (http://localhost:8000/telemetry)**

- Navbar renders, Utilities pill is active
- SVG gauges render correctly
- Canvas histograms render correctly
- Hardware / Performance tab toggle works
- Stuck tasks modal works

- [ ] **Step 4: Commit**

```bash
git add frontend/telemetry.html
git commit -m "feat(ui): add LCARS navbar to telemetry.html; harmonize color palette"
```

---

## Chunk 6: Final Cleanup

### Task 11: Cleanup — remove mockups, update nav links, final checks

**Files:**
- Delete: `frontend/static/mockup-*.html`
- Verify: all internal links updated

- [ ] **Step 1: Check for any remaining /catalog or /index links across all frontend files**

```bash
grep -r "/catalog\|/index\|index\.html" frontend/ --include="*.html" -l
```

Update any found references to `/vault`.

- [ ] **Step 2: Check for remaining Tailwind CDN references**

```bash
grep -r "tailwindcss" frontend/ --include="*.html"
```

Expected: no results.

- [ ] **Step 3: Remove mockup files**

```bash
git rm frontend/static/mockup-*.html
```

- [ ] **Step 4: Full smoke test — visit every page**

| URL | Expected |
|---|---|
| `http://localhost:8000/` | vault.html, Vault pill active |
| `http://localhost:8000/vault` | same |
| `http://localhost:8000/catalog` | 301 → /vault |
| `http://localhost:8000/search` | search.html, Search pill active |
| `http://localhost:8000/utils` | utils.html, Utilities pill active |
| `http://localhost:8000/optimizer` | optimizer.html, Utilities pill active |
| `http://localhost:8000/settings` | settings.html, Settings pill active |
| `http://localhost:8000/lab` | lab.html, Utilities pill active |
| `http://localhost:8000/telemetry` | telemetry.html, Utilities pill active |
| `http://localhost:8000/identity` | identity.html, Identity pill active |
| Sensor rail | All pages show live CPU/GPU/Temp/Workers values |

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore(ui): remove mockup files; final LCARS overhaul cleanup"
```
