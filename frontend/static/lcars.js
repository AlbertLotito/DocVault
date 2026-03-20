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
      const paused  = wrk.paused   ?? false;
      const stalled = wrk.stalled  ?? false;
      const stallM  = wrk.stall_minutes ?? 0;
      const ep      = wrk.embedding_progress ?? null;
      if (stalled) {
        _setSensor('lc-s-workers', `⚠ STALLED ${stallM}m`, 'err');
      } else if (paused) {
        _setSensor('lc-s-workers', '⏸ PAUSED', 'warn');
      } else if (ep) {
        const m = (ep.progress_text || '').match(/(\d+)\/(\d+)/);
        const label = m ? `▶ ${ep.pct}% · ${m[1]}/${m[2]}` : '▶ RUNNING';
        _setSensor('lc-s-workers', label, 'ok');
      } else {
        _setSensor('lc-s-workers', '▶ RUNNING', 'ok');
      }
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
