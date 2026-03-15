# DocVault UI Overhaul — LCARS Design System
**Date:** 2026-03-15
**Status:** Approved by user

---

## 1. Design Language

### Palette — Warm LCARS
| Role | Color | Usage |
|---|---|---|
| Accent / primary | `#ff9900` | Values, active state, brand |
| Accent / dim | `#cc6600` | Section titles, borders, elbow |
| Label text | `#aa7744` | Secondary labels, table headers |
| Muted label | `#996633` | Sensor labels, de-emphasized text |
| Background / deep | `#000000` | Navbar, pure black areas |
| Background / page | `#0a0600` | Page body |
| Background / surface | `#0d0800` | Cards, tiles, surfaces |
| Background / panel | `#080400` | Recessed panels, log section |
| Border | `#1a0d00` | Surface borders |
| Border / strong | `#331a00` | Section dividers |
| Status / ok | `#33ee77` | Workers active, connected |
| Status / warn | `#ffcc66` | Temp warnings, degraded |
| Status / error | `#ff4444` | Errors, failures |
| Status / processing | `#9966ff` | In-progress tasks |
| Status / pending | `#ccaa00` | Queued tasks |
| Status / unknown | `#666666` | Unrecognised file types |
| Archived / dim | `#333333` border, `opacity: 0.5` | Archived vault cards |

### Typography
- **Font:** `'Arial Narrow', Arial, sans-serif` — all pages
- **Minimum label size:** 10px (11px preferred)
- **Value / data text:** 13–22px depending on context, `#ff9900` or brighter
- **Section titles:** 11px, `#cc6600`, uppercase, `letter-spacing: 3px`
- **Never use:** 8px or 9px for any visible text

### LCARS Structural Elements
- **Elbow:** Rounded bottom-right corner on a solid amber block (`border-radius: 0 0 22px 0`), contains "DV" monogram
- **Pill nav links:** `border-radius: 10px`, amber border + dark bg, active = solid amber fill
- **Stat tiles:** Left accent border (3px, color-coded by status), dark surface
- **Bar badges:** Full-width horizontal pill — colored circular left tab + dark body with name, description, arrow
- **Section divider line:** `::after` pseudo-element, `height: 1px`, `#1a0d00`

---

## 2. Global Navigation

### Shared JS — lcars.js
All pages load `frontend/static/lcars.js` alongside `lcars.css`. This file contains:
- Sensor rail polling (`GET /api/monitor/status` + `GET /api/workers/status`, every 5s)
- Active nav pill detection (sets `.active` on the pill whose `href` matches `location.pathname`)
- Sub-pages rule: `lab.html`, `telemetry.html`, `optimizer.html` → highlight the **Utilities** pill as active parent

### Two-Row Navbar (all pages)
```
┌────────────────────────────────────────────────────────────┐
│ [DV elbow] DocVault   [Search] [Vault] [Identity] [Utils] [Settings]  │  ← 40px, border-bottom: 1px #331a00
├────────────────────────────────────────────────────────────┤
│  Workers ● 2   CPU 34%   GPU 0%   Temp 61°C        State ▐ READY     │  ← 22px, border-bottom: 3px #ff9900
└────────────────────────────────────────────────────────────┘
```

**Top row (40px):**
- Elbow (`#cc6600`, 58px wide, `border-radius: 0 0 22px 0`)
- Brand: "DocVault" (`#ff9900`, 13px, bold, `letter-spacing: 3px`)
- Nav pills: Search · Vault · Identity · Utilities · Settings
- Active pill: solid `#cc6600` fill, black text, bold
- Inactive pill: `#1a0d00` bg, `#ff9900` text, `#663300` border

**Bottom row (22px) — sensor rail:**
- Labels: 11px, `#996633`, uppercase
- Values: 11px, `#ffcc66`; ok state = `#33ee77`
- Sensors: Workers, CPU, GPU, Temp, Disk, State (right-aligned)
- Bottom border: 3px solid `#ff9900` (the LCARS "stripe")

**Nav items (5):** Search · Vault · Identity · Utilities · Settings

---

## 3. Page Inventory

### 3.1 Vault (merged index.html + catalog.html → vault.html)

Replaces both `index.html` (Vault Status) and `catalog.html` (Vault Log). Single page, horizontal split.

**URL / routing:**
- `/` and `/status` → serve `vault.html`
- `/catalog` → HTTP 301 redirect to `/vault` (preserves bookmarks)
- `/vault` → serve `vault.html`
- Query params forwarded: `/vault?status=ERROR` auto-selects the Error filter pill in the log section and scrolls to it

**Top half — Vault Status:**
- 6-tile stat grid: Completed, Processing, Pending, Error, Unknown, Total (color-coded left borders)
- Vault cards row: one card per vault, scrollable horizontally
  - Active vaults: full brightness, amber top border
  - Archived vaults: `opacity: 0.5`, gray top border (`#333`)
  - Each card shows: name, path, file count, completion %, status badge
- Worker controls (pause/resume/reset) — small amber action buttons, right-aligned

**Divider:** 3px `#331a00` line with centered "Vault Log" label

**Bottom half — Vault Log:**
- Filter pills (bar): All · Completed · Processing · Error · Unknown · PDF · Image · …
- Sortable table: File, Type, Vault, Status, Size, Updated, Actions (⋯ context menu)
- Status column: color-coded pill badges
- Right-click context menu (existing behavior preserved): open file, open folder, inspect, reprocess
- **Stat tile clicks:** clicking a stat tile (e.g. Error) scrolls to the log section and pre-activates the matching filter pill — equivalent to `/vault?status=ERROR`

### 3.2 Search (search.html)

**Bar badges across top — mode selector:**
```
[🔍 Content Search ▶]  [📁 Filename Search ▶]  [💬 Ask ▶]
```
- Three bar badges, only one active at a time (active = amber tab + highlighted body)
- Active mode's interface appears below the badge row
- Each mode retains its current functionality (vault selector, filters, results list, chat history)

### 3.3 Utilities (utils.html → hub page)

Bar badges, one per tool. Each badge navigates to a full standalone page (no overlays):

| Tab color | Tool | Destination |
|---|---|---|
| `#cc6600` | ⚗ Extractor Lab | `lab.html` |
| `#886600` | 📡 Telemetry | `telemetry.html` |
| `#664400` | ⚙ Optimizer | `optimizer.html` |
| `#443300` | 🩺 Health Check | inline expand — `/utils/health` system check (Ollama, Tesseract, Poppler) |
| `#334400` | ☁ Google Drive | inline expand — OAuth authenticate action |
| `#2a3300` | ⬡ Qdrant | inline expand — `/utils/qdrant_check` vector store health + collection info |

"Inline expand" tools open their content inline below the badge (no page nav) — they're lightweight status/action panels, not full tools.

### 3.4 Settings (settings.html)

Bar badges replace the sidebar. Each badge = one settings group. Clicking expands the group's settings inline below it (accordion). Only one group open at a time.

Settings groups (14 → mapped to bar badges):
General · Ollama · Qdrant · PDF · Embeddings · Search · Vision · Video · Google · Bing · HuggingFace · Art · Monitor · Lab

Each expanded group shows its settings in the same layout as today (label + input + description + save button), styled in LCARS: dark surface, amber borders, amber labels.

**Accordion badge states:**
- **Closed:** arrow `▶`, pill shape fully rounded, body normal
- **Open:** arrow `▼`, bottom corners of the badge flatten (`border-radius: 27px 4px 0 0`) so the expanded content panel flows directly below without a gap

### 3.5 Identity (identity.html)

LCARS skin applied: navbar, dark background, amber accents. Card grid preserved. Thumbnail cards get amber top border and dark surface. Sighting count overlay styled in amber.

**Deep links:** "Find Photos" links currently point to `/catalog?query=<name>`. These become `/vault?query=<name>` — the vault log pre-filters by filename match on load.

### 3.6 Lab (lab.html → standalone full page)

Already dark-themed — apply Warm LCARS palette uniformly (replace emerald/slate tones with amber/orange). Add two-row navbar. Remove overlay chrome (close button, backdrop).

### 3.7 Telemetry (telemetry.html → standalone full page)

Already dark-themed — apply Warm LCARS palette. Add two-row navbar. Remove fullscreen-only chrome; page is now always full-width within the layout.

### 3.8 Optimizer (new optimizer.html → standalone full page)

Extract from utils.html overlay into its own page. Add two-row navbar. Retain existing Q1–Q4 grid, log panel, parameter controls, profile buttons.

---

## 4. Component Library

### Bar Badge
```html
<div class="bar-badge">
  <div class="bar-tab" style="background: #cc6600;">⚗</div>
  <div class="bar-body">
    <div class="bar-name">Extractor Lab</div>
    <div class="bar-desc">Test kernels, certify extractors, simulate pipelines</div>
    <div class="bar-arrow">▶</div>
  </div>
</div>
```
- Height: 54px; tab width: 54px; `border-radius: 27px 4px 4px 27px`
- Name: 14px, `#ff9900`, bold, uppercase; Desc: 11px, `#aa7744`
- Active state: body bg lightens to `#110a00`, arrow becomes `#ff9900`

### Stat Tile
- 3px left border (status color), dark surface `#0d0800`
- Value: 20–22px bold `#ff9900`; Label: 10px `#aa7744` uppercase

### Vault Card
- Top border 3px (vault color or `#cc6600`), dark surface
- Archived: `opacity: 0.5`, top border `#333`

### Status Pill Badges (log table)
| Status | bg | text | border |
|---|---|---|---|
| Completed | `#002211` | `#00cc66` | `#005533` |
| Processing | `#110033` | `#9966ff` | `#330066` |
| Error | `#220000` | `#ff4444` | `#550000` |
| Pending | `#111100` | `#ccaa00` | `#333300` |
| Unknown | `#0a0a0a` | `#666` | `#222` |

---

## 5. Shared CSS File

All pages load a single shared stylesheet: `frontend/static/lcars.css`

Contains: CSS custom properties (palette vars), navbar, pill nav, bar badge, stat tile, vault card, status badges, filter pills, section title, sensor rail.

Each page's unique layout is defined in an inline `<style>` block or page-specific file. Pages do **not** load Tailwind CDN — replace with `lcars.css` + minimal page styles.

---

## 6. File Changes Summary

| File | Action |
|---|---|
| `frontend/static/lcars.css` | **Create** — shared design system |
| `frontend/index.html` | **Replace** → `vault.html` (merged status + log) |
| `frontend/catalog.html` | **Remove** (merged into vault.html) |
| `frontend/search.html` | **Restyle** — bar badge mode tabs |
| `frontend/utils.html` | **Restyle** — hub with bar badges, remove overlays |
| `frontend/settings.html` | **Restyle** — bar badge accordion groups |
| `frontend/identity.html` | **Restyle** — LCARS skin |
| `frontend/lab.html` | **Restyle** — full page, add navbar, Warm LCARS palette |
| `frontend/telemetry.html` | **Restyle** — full page, add navbar, Warm LCARS palette |
| `frontend/optimizer.html` | **Create** — extracted from utils.html overlay |
| `api/main.py` | `/` and `/status` → serve `vault.html`; add `/vault` route; add `/catalog` → 301 `/vault`; add `/optimizer` route |
| `api/routes/` | Update any hardcoded `/catalog` or `/index` redirects to `/vault` |

---

## 7. Out of Scope

- No changes to backend API, database schema, or extraction logic
- No new features — purely visual/structural rework
- Existing JS functionality preserved in each page (polling, modals, context menus, etc.)
