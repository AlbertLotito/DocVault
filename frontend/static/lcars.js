/* frontend/static/lcars.js — Shared navbar + sensor rail for all DocVault pages */

let _i18n    = {};
let _locales = [];
let _activeLang = 'en';

/**
 * NAVBAR HTML injected into every .lc-nav container.
 * data-page attribute on <body> sets the active pill.
 * Sub-pages: lab, telemetry, optimizer → 'utilities' parent active.
 */
function buildNavHTML() {
  return `
<div class="lc-nav-top">
  <a href="/" class="lc-elbow">DV</a>
  <a href="/" class="lc-brand">DocVault</a>
  <nav class="lc-nav-links">
    <a href="/search"   class="lc-pill" data-nav="search">${t('nav.search')}</a>
    <a href="/vault"    class="lc-pill" data-nav="vault">${t('nav.vault')}</a>
    <a href="/identity" class="lc-pill" data-nav="identity">${t('nav.identity')}</a>
    <a href="/utils"    class="lc-pill" data-nav="utilities">${t('nav.utilities')}</a>
    <a href="/settings" class="lc-pill" data-nav="settings">${t('nav.settings')}</a>
  </nav>
</div>
<div class="lc-nav-bot" id="lc-sensor-rail">
  <span class="lc-sensor" id="lc-s-workers">${t('sensor.workers')} <span>—</span></span>
  <span class="lc-sensor" id="lc-s-cpu">${t('sensor.cpu')} <span>—</span></span>
  <span class="lc-sensor" id="lc-s-gpu">${t('sensor.gpu')} <span>—</span></span>
  <span class="lc-sensor" id="lc-s-temp">${t('sensor.temp')} <span>—</span></span>
  <span class="lc-sensor" id="lc-s-disk">${t('sensor.disk')} <span>—</span></span>
  <span class="lc-sensor lc-sensor-push" id="lc-s-state">${t('sensor.state')} <span>—</span></span>
</div>`;
}

/** Pages that live under Utilities in the nav hierarchy */
const UTILITIES_SUBPAGES = ['lab', 'telemetry', 'optimizer'];

function t(key, vars = {}) {
  let s = _i18n[key] ?? key;
  for (const [k, v] of Object.entries(vars))
    s = s.replaceAll(`{${k}}`, v);
  return s;
}

async function lcI18nLoad() {
  try {
    const r = await fetch('/static/i18n/manifest.json');
    if (r.ok) _locales = await r.json();
  } catch (_) {}
  if (!_locales.length) _locales = [{ code: 'en', label: 'EN' }];

  let lang = '';
  try {
    const r = await fetch('/api/settings');
    if (r.ok) {
      const data = await r.json();
      const entry = (data.settings || []).find(s => s.key === 'ui:language');
      if (entry) lang = entry.value || '';
    }
  } catch (_) {}
  if (!lang) lang = (navigator.language || 'en').split('-')[0];
  if (!_locales.find(l => l.code === lang)) lang = 'en';
  _activeLang = lang;

  for (const candidate of lang === 'en' ? ['en'] : [lang, 'en']) {
    try {
      const r = await fetch(`/static/i18n/${candidate}.json`);
      if (r.ok) { _i18n = await r.json(); return; }
    } catch (_) {}
  }
}

function lcApplyI18n() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const firstText = [...el.childNodes].find(n => n.nodeType === Node.TEXT_NODE);
    if (firstText) {
      firstText.textContent = t(el.dataset.i18n);
    } else {
      el.appendChild(document.createTextNode(t(el.dataset.i18n)));
    }
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  document.querySelectorAll('[data-i18n-title]').forEach(el => {
    el.title = t(el.dataset.i18nTitle);
  });
}

function lcBuildLangPicker(navEl) {
  const rail = navEl.querySelector('#lc-sensor-rail');
  if (!rail) return;
  const wrap = document.createElement('span');
  wrap.className = 'lc-sensor lc-lang-picker';
  wrap.id = 'lc-s-lang';
  const sel = document.createElement('select');
  sel.id = 'lc-lang-select';
  sel.setAttribute('aria-label', 'Language');
  _locales.forEach(loc => {
    const opt = document.createElement('option');
    opt.value = loc.code;
    opt.textContent = loc.label;
    if (loc.code === _activeLang) opt.selected = true;
    sel.appendChild(opt);
  });
  sel.addEventListener('change', async () => {
    try {
      await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: 'ui:language', value: sel.value }),
      });
    } catch (_) { /* best-effort; reload anyway */ }
    location.reload();
  });
  wrap.appendChild(sel);
  rail.appendChild(wrap);
}

async function lcInit() {
  await lcI18nLoad();
  const navEl = document.querySelector('.lc-nav');
  if (navEl) {
    navEl.innerHTML = buildNavHTML();

    // Set active pill
    const page = document.body.dataset.page || '';
    const activeKey = UTILITIES_SUBPAGES.includes(page) ? 'utilities' : page;
    const pill = navEl.querySelector(`[data-nav="${activeKey}"]`);
    if (pill) pill.classList.add('active');
    lcBuildLangPicker(navEl);
  }
  lcApplyI18n();

  // Start sensor rail polling
  lcPollSensors();
  setInterval(lcPollSensors, 5000);

  // Init shared toast
  if (!document.getElementById('lc-toast')) {
    const toastEl = document.createElement('div');
    toastEl.id = 'lc-toast';
    toastEl.className = 'lc-toast';
    document.body.appendChild(toastEl);
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
  const toastEl = document.getElementById('lc-toast');
  if (!toastEl) return;
  toastEl.textContent = msg;
  toastEl.className = 'lc-toast' + (isErr ? ' err' : '');
  // force reflow
  void toastEl.offsetWidth;
  toastEl.classList.add('show');
  clearTimeout(toastEl._timer);
  toastEl._timer = setTimeout(() => toastEl.classList.remove('show'), 2800);
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

document.addEventListener('DOMContentLoaded', () => lcInit());
