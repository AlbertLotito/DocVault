// Shared utilities for all pages

async function api(path, options = {}) {
  const resp = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!resp.ok) {
      const errorText = await resp.text();
      throw new Error(`API error ${resp.status}: ${errorText}`);
  }
  return resp.json();
}

function statusBadge(status) {
  return `<span class="status-badge status-${status || 'UNKNOWN'}">${status || 'N/A'}</span>`;
}

function formatPath(path) {
  return path ? path.split(/[\\/]/).pop() : '—';
}

async function openFile(path) {
  try {
    const r = await api('/utils/open_path', {
      method: 'POST',
      body: JSON.stringify({ path, action: 'file' }),
    });
    if (r.status === 'error') alert(`Could not open: ${r.detail}`);
  } catch(e) {
    alert(`Could not open: ${e.message}`);
  }
}

async function openFolder(path) {
  try {
    const r = await api('/utils/open_path', {
      method: 'POST',
      body: JSON.stringify({ path, action: 'folder' }),
    });
    if (r.status === 'error') alert(`Could not open: ${r.detail}`);
  } catch(e) {
    alert(`Could not open: ${e.message}`);
  }
}

// ── Notifications (Alert Bus) ───────────────────────────────────────────────

let lastAlertId = 0;

function _ensureToastContainer() {
  if (document.getElementById('toast-container')) return;
  const container = document.createElement('div');
  container.id = 'toast-container';
  container.className = 'fixed bottom-6 right-6 z-[9999] flex flex-col gap-3 pointer-events-none';
  document.body.appendChild(container);

  // Add Toast Styles dynamically
  const style = document.createElement('style');
  style.textContent = `
    .toast-card {
      pointer-events: auto;
      min-width: 320px;
      max-width: 450px;
      animation: toast-in 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275) forwards;
    }
    @keyframes toast-in {
      from { opacity: 0; transform: translateX(100%) scale(0.9); }
      to { opacity: 1; transform: translateX(0) scale(1); }
    }
    .toast-out {
      animation: toast-out 0.3s ease forwards;
    }
    @keyframes toast-out {
      to { opacity: 0; transform: translateX(20%) scale(0.95); }
    }
  `;
  document.head.appendChild(style);
}

function showToast(title, message, level = 'info') {
  _ensureToastContainer();
  const container = document.getElementById('toast-container');
  
  const colors = {
    info:     'bg-indigo-600',
    success:  'bg-emerald-600',
    warning:  'bg-amber-500',
    error:    'bg-rose-600',
    critical: 'bg-black border-2 border-rose-600 animate-pulse'
  };
  const color = colors[level] || colors.info;

  const toast = document.createElement('div');
  toast.className = `toast-card ${color} text-white p-4 rounded-lg shadow-2xl flex flex-col gap-1 cursor-pointer`;
  toast.innerHTML = `
    <div class="flex justify-between items-center">
      <span class="text-[10px] font-bold uppercase tracking-widest opacity-80">${level}</span>
      <button class="text-white/50 hover:text-white leading-none">&times;</button>
    </div>
    <div class="font-bold text-sm leading-tight">${_esc(title)}</div>
    <div class="text-xs opacity-90 leading-normal">${_esc(message)}</div>
  `;

  const dismiss = () => {
    toast.classList.add('toast-out');
    setTimeout(() => toast.remove(), 300);
  };
  toast.onclick = dismiss;
  container.appendChild(toast);

  if (level !== 'critical') {
    setTimeout(dismiss, 8000);
  }
}

async function pollAlerts() {
  try {
    const alerts = await api(`/utils/alerts?since_id=${lastAlertId}`);
    for (const a of alerts) {
      showToast(a.title, a.message, a.level);
      if (a.id > lastAlertId) lastAlertId = a.id;
    }
  } catch (err) {
    console.error("Alert poll failed", err);
  }
}

// Start notification poller
setInterval(pollAlerts, 10000);
setTimeout(pollAlerts, 1000); // Quick first check

// ── Inspect modal ────────────────────────────────────────────────────────────

function _esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function _ensureInspectModal() {
  if (document.getElementById('inspect-modal')) return;
  const wrap = document.createElement('div');
  wrap.id = 'inspect-modal';
  wrap.innerHTML = `
    <div id="inspect-overlay" class="hidden fixed inset-0 z-40 bg-black/40" onclick="closeInspect()"></div>
    <div id="inspect-panel"
      class="hidden fixed z-50 top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2
             bg-white rounded-xl shadow-2xl flex flex-col overflow-hidden"
      style="width:min(900px,92vw);height:min(720px,90vh)">
      <div class="flex items-center justify-between px-5 py-3 border-b border-gray-100 shrink-0 gap-3">
        <div class="min-w-0 flex items-center gap-2">
          <span id="inspect-fname" class="font-bold text-gray-800 truncate"></span>
          <span id="inspect-ftype" class="shrink-0 text-xs font-mono bg-gray-100 px-1.5 py-0.5 rounded text-gray-500"></span>
        </div>
        <button onclick="closeInspect()" class="shrink-0 text-gray-400 hover:text-gray-700 text-2xl leading-none">&times;</button>
      </div>
      <div id="inspect-body" class="flex-1 overflow-y-auto px-5 py-4 space-y-6 text-sm"></div>
    </div>`;
  document.body.appendChild(wrap);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeInspect(); });
}

async function inspectFile(path) {
  _ensureInspectModal();
  document.getElementById('inspect-body').innerHTML =
    '<p class="text-gray-400 animate-pulse py-8 text-center">Loading…</p>';
  document.getElementById('inspect-fname').textContent = path.split(/[\\/]/).pop();
  document.getElementById('inspect-ftype').textContent = '';
  document.getElementById('inspect-overlay').classList.remove('hidden');
  document.getElementById('inspect-panel').classList.remove('hidden');
  try {
    const data = await api(`/catalog/inspect?path=${encodeURIComponent(path)}`);
    _renderInspect(data);
  } catch(e) {
    document.getElementById('inspect-body').innerHTML =
      `<p class="text-red-500 py-4">Error: ${_esc(e.message)}</p>`;
  }
}

function closeInspect() {
  document.getElementById('inspect-overlay')?.classList.add('hidden');
  document.getElementById('inspect-panel')?.classList.add('hidden');
}

function _renderInspect(data) {
  const t      = data.task   || {};
  const chunks = data.chunks || [];
  const images = data.images || [];

  document.getElementById('inspect-fname').textContent =
    (t.file_path || '').split(/[\\/]/).pop() || '?';
  document.getElementById('inspect-ftype').textContent = t.file_type || '?';

  const statusColors = {
    COMPLETED: 'bg-green-100 text-green-700',
    EXTRACTED: 'bg-blue-100 text-blue-700',
    EMBEDDING: 'bg-blue-100 text-blue-700',
    PENDING:   'bg-yellow-100 text-yellow-700',
    ERROR:     'bg-red-100 text-red-700',
    UNKNOWN:   'bg-gray-100 text-gray-500',
  };
  const sc = statusColors[t.status] || 'bg-gray-100 text-gray-500';

  function fmtSize(b) {
    if (!b) return '—';
    if (b < 1024) return `${b} B`;
    if (b < 1048576) return `${(b/1024).toFixed(1)} KB`;
    return `${(b/1048576).toFixed(1)} MB`;
  }
  function fmtDate(s) {
    if (!s) return '—';
    try { return new Date(s).toLocaleString(); } catch { return s; }
  }

  let html = '';

  // ── File record ──────────────────────────────────────────────────────────
  html += `
    <div>
      <h3 class="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">File Record</h3>
      <div class="grid grid-cols-2 gap-x-8 gap-y-2 text-xs">
        <div class="flex gap-2"><span class="text-gray-400 w-20 shrink-0">Status</span>
          <span class="px-1.5 py-0.5 rounded font-medium ${sc}">${_esc(t.status) || '—'}</span></div>
        <div class="flex gap-2"><span class="text-gray-400 w-20 shrink-0">Priority</span>
          <span>${t.priority ?? '—'}</span></div>
        <div class="flex gap-2"><span class="text-gray-400 w-20 shrink-0">Size</span>
          <span>${fmtSize(t.file_size)}</span></div>
        <div class="flex gap-2"><span class="text-gray-400 w-20 shrink-0">Last updated</span>
          <span>${fmtDate(t.last_update)}</span></div>
        <div class="flex gap-2"><span class="text-gray-400 w-20 shrink-0">Created</span>
          <span>${fmtDate(t.file_created)}</span></div>
        <div class="flex gap-2"><span class="text-gray-400 w-20 shrink-0">Modified</span>
          <span>${fmtDate(t.file_modified)}</span></div>
        <div class="col-span-2 flex gap-2 mt-1"><span class="text-gray-400 w-20 shrink-0">Path</span>
          <span class="text-gray-600 break-all">${_esc(t.file_path) || '—'}</span></div>
        <div class="col-span-2 flex gap-2"><span class="text-gray-400 w-20 shrink-0">Hash</span>
          <span class="font-mono text-gray-400 break-all text-xs">${_esc(t.file_hash) || '—'}</span></div>
      </div>
    </div>`;

  // ── Error log ────────────────────────────────────────────────────────────
  if (t.error_log) {
    html += `
      <div>
        <h3 class="text-xs font-semibold text-red-400 uppercase tracking-wide mb-2">Error Log</h3>
        <pre class="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-3 whitespace-pre-wrap overflow-x-auto">${_esc(t.error_log)}</pre>
      </div>`;
  }

  // ── Extracted text ───────────────────────────────────────────────────────
  if (t.extracted_text) {
    html += `
      <div>
        <div class="flex items-center justify-between mb-2">
          <h3 class="text-xs font-semibold text-gray-400 uppercase tracking-wide">Extracted Text</h3>
          <span class="text-xs text-gray-400">${t.extracted_text.length.toLocaleString()} chars</span>
        </div>
        <pre class="text-xs text-gray-700 bg-gray-50 rounded p-3 max-h-52 overflow-y-auto whitespace-pre-wrap">${_esc(t.extracted_text)}</pre>
      </div>`;
  }

  // ── Vector chunks ────────────────────────────────────────────────────────
  html += `
    <div>
      <div class="flex items-center gap-2 mb-2">
        <h3 class="text-xs font-semibold text-gray-400 uppercase tracking-wide">Vector Chunks</h3>
        <span class="text-xs bg-indigo-100 text-indigo-700 font-mono px-1.5 py-0.5 rounded">${chunks.length}</span>
      </div>`;
  if (chunks.length === 0) {
    html += `<p class="text-xs text-gray-400">No vectors indexed for this file.</p>`;
  } else {
    html += `<div class="space-y-1.5">`;
    for (const c of chunks) {
      const preview = c.text.length > 220 ? c.text.slice(0, 220) + '…' : c.text;
      html += `
        <div class="bg-gray-50 rounded p-2.5">
          <span class="text-xs text-gray-400 font-mono">chunk ${c.index}</span>
          <p class="text-xs text-gray-600 mt-0.5">${_esc(preview)}</p>
        </div>`;
    }
    html += `</div>`;
  }
  html += `</div>`;

  // ── Extracted images ─────────────────────────────────────────────────────
  if (images.length > 0) {
    html += `
      <div>
        <div class="flex items-center gap-2 mb-2">
          <h3 class="text-xs font-semibold text-gray-400 uppercase tracking-wide">Extracted Images</h3>
          <span class="text-xs bg-purple-100 text-purple-700 font-mono px-1.5 py-0.5 rounded">${images.length}</span>
        </div>
        <div class="space-y-1.5">`;
    for (const img of images) {
      const fname = (img.file_path || '').split(/[\\/]/).pop();
      html += `
          <div class="bg-gray-50 rounded p-2.5 flex items-center justify-between gap-4">
            <div class="text-xs min-w-0">
              <span class="font-mono text-gray-700 truncate block">${_esc(fname)}</span>
              <span class="text-gray-400">page ${img.page_num ?? '?'} · ${img.width ?? '?'}×${img.height ?? '?'}</span>
            </div>
            <div class="flex gap-1 shrink-0">
              <button data-path="${_esc(img.file_path).replace(/"/g,'&quot;')}" onclick="openFile(this.dataset.path)"
                class="text-xs px-1.5 py-0.5 rounded bg-gray-200 hover:bg-indigo-100 text-gray-500 hover:text-indigo-700">📄</button>
              <button data-path="${_esc(img.file_path).replace(/"/g,'&quot;')}" onclick="openFolder(this.dataset.path)"
                class="text-xs px-1.5 py-0.5 rounded bg-gray-200 hover:bg-indigo-100 text-gray-500 hover:text-indigo-700">📁</button>
            </div>
          </div>`;
    }
    html += `</div></div>`;
  }

  document.getElementById('inspect-body').innerHTML = html;
}
