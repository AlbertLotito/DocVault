# Tray Search Popup — Design Spec
**Date:** 2026-08-02
**Status:** Approved

---

## 1. Problem

The system tray icon (`tray/tray_app.py`) currently only opens the full DocVault web UI. There's no quick way to search without switching to a browser tab and navigating to the Search page. A lightweight, always-available search popup reachable directly from the tray menu removes that friction for quick lookups.

## 2. Goals

- A new **"Search..."** tray menu item opens a small, floating, non-modal window near the mouse cursor.
- The popup has: a text input, a search-type dropdown, and a results pane below.
- Search types: **Hybrid**, **Full-text**, **Semantic** (all three hit the existing content-search endpoint with a different `mode`), and **Filename**.
- Double-clicking a result opens the file via the server's existing path-validated open endpoint.
- Multiple popups can be open simultaneously — each "Search..." click spawns an independent window; no single-instance enforcement.
- Plain native (Tk) look — no attempt to reproduce the LCARS web theme.

## 3. Non-Goals

- No "Ask" (RAG) mode in this popup — RAG is streaming and conversational, a poor fit for a quick lookup window. It's explicitly deferred to a possible future standalone chat feature.
- No visual theming/skinning to match `lcars.css` — a functional native Tk window is sufficient.
- No remembered window position or single-instance/focus-existing behavior — each invocation is independent, positioned at the current cursor location.
- No new "open file" logic in the tray process — reuses the server's existing `POST /utils/open_path` (vault-root validation, blocked-extension check) rather than duplicating that security logic locally.
- No offline/local search fallback — if the DocVault server isn't reachable, the popup shows an inline error; it does not queue or retry.

## 4. Architecture

`tray_app.py` today calls `icon.run()` on the main thread (pystray's blocking event loop) and does nothing else. This feature adds a second, always-running participant in the same process:

- **At startup**, alongside building the tray icon, spawn one background thread that creates a single hidden `tk.Tk()` root and calls `root.mainloop()`. This root is never shown — it exists only to own the one Tcl interpreter/event loop that all popups live under.
- **A thread-safe `queue.Queue`** is the only channel between pystray's callback thread and the Tk thread. The hidden root polls it every ~100ms via `root.after(100, _drain_queue)`.
- **The "Search..." menu callback** (invoked by pystray on its own internal thread) does no UI work directly — it just captures the current cursor position and puts a `"spawn"` message on the queue.
- **`_drain_queue`** (running on the Tk thread) pops any pending `"spawn"` messages and, for each, creates a new `Toplevel` positioned near the captured cursor coordinates.

This keeps pystray's existing structure (`main()`, `icon.run()`) untouched, and avoids relying on the undocumented/flaky behavior of multiple independent `Tk()` instances across threads — every popup is a `Toplevel` under the one interpreter, which is a supported Tkinter pattern.

## 5. Popup Window

Each `Toplevel` is self-contained and holds:

- An `Entry` for the query text. Enter key triggers the search (matches the main Search page's existing "Enter to search" convention).
- A `ttk.Combobox` (read-only) for search type: `Hybrid` (default) / `Full-text` / `Semantic` / `Filename`.
- A `Button` to trigger search explicitly (for mouse-only use).
- A results pane below — a scrollable frame of result rows (filename bold, path dimmed/truncated, snippet text), built with a `Canvas` + `Frame` + `Scrollbar` (or a `Listbox` if a simpler single-line-per-result rendering turns out to be sufficient — decided during implementation based on how snippet text actually looks).
- Non-modal: no `grab_set()` — the user can freely switch focus to other windows while it's open. `wm_attributes('-topmost', True)` is used so it doesn't get lost behind the main window immediately on open, but it does not stay pinned above everything permanently — topmost is set once at creation, not continuously re-asserted.

### Search execution

- On submit, the query + selected mode are sent to the already-running DocVault server over HTTP using `httpx` (already a project dependency — see `llm/factory.py`'s Claude provider for existing usage), on a background thread (via `threading.Thread`, one-shot per search) so the Tk mainloop is never blocked waiting on the network.
- Hybrid/Full-text/Semantic → `GET /search?q=<query>&mode=<hybrid|fts|semantic>`
- Filename → `GET /search/filename?q=<query>`
- The base URL is read the same way `tray_app.py` already reads it for "Open DocVault" — `get_server_url()` from `config.ini`, at call time (not cached at import time, matching the project's settings convention).
- When the HTTP response arrives on the background thread, results are handed back to the Tk thread via the same queue-drain mechanism (a `"results"` message tagged with which `Toplevel` it belongs to) — Tkinter widgets must only be touched from the Tk thread.
- Errors (connection refused, timeout, non-200) render as a single inline message in the results pane: e.g. "Could not reach DocVault server." No retry/backoff — the user can just search again.

### Opening a result

- Double-click on a result row calls `POST /utils/open_path` with `{"path": <file_path>, "action": "file"}`, again via a background thread (fire-and-forget; the response is only used to show an inline error if `status != "ok"`).
- This reuses the server's existing vault-root + blocked-extension validation rather than re-implementing it in the tray process.

## 6. Error Handling

| Condition | Behavior |
|---|---|
| Server unreachable / timeout | Inline text in results pane: "Could not reach DocVault server." |
| Empty query submitted | No request sent; nothing happens (matches main Search page's `min_length=1` requirement — avoid a guaranteed 422). |
| Search returns zero results | Inline text: "No results." |
| `degraded: true` in response (semantic search down, falls back to FTS) | Inline note shown above results using the response's own `degraded_reason` string — same data the main Search page already surfaces. |
| `open_path` returns `status: "error"` | Inline text in the popup: the server's own `detail` string (e.g. "Path is outside vault boundaries"). |

## 7. Testing / Verification

No existing automated test exercises tray GUI behavior (per the prior tray feature's status doc — Tk/pystray GUI isn't practically unit-testable the way the FastAPI routes are). Split:

- **Automated**: none of the new Tk/threading code is unit tested directly (consistent with `tray_app.py`'s existing test coverage, which only covers `get_server_url()` config parsing). If `_drain_queue`'s message-routing logic ends up non-trivial, a small pure-function unit test (queue in → expected dispatch, no real Tk widgets) may be added during implementation — decided then, not speced in advance.
- **Manual verification** (post-implementation, by the user):
  1. Click "Search..." — popup appears near the cursor, non-modal (can still interact with other windows).
  2. Open two popups at once — both function independently.
  3. Search each mode (Hybrid/Full-text/Semantic/Filename) against real vault content — results appear.
  4. Double-click a result — file opens in its default app.
  5. Stop the DocVault server, search again — inline error shown, no crash.
  6. Submit an empty query — no error, nothing happens.

## 8. Rollout

Purely additive to `tray/tray_app.py` (no new file — this is small enough to live in the same module) plus the existing tray menu gains one item. No new dependency (`tkinter` is stdlib, `httpx` already required). No server-side changes needed — both endpoints it calls already exist.
