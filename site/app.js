/* ===== NOVA — Frontend Logic ===== */

const WORKER = 'https://novaautomation.asifaliasmr05.workers.dev';
const $ = id => document.getElementById(id);

/* ===== API ===== */
async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json' };
  const token = localStorage.getItem('nova_session');
  if (token) headers['Authorization'] = 'Bearer ' + token;
  const resp = await fetch(WORKER + path, { ...opts, headers });
  if (resp.status === 401 && !path.startsWith('/api/auth/')) {
    localStorage.removeItem('nova_session');
    showAuth();
    throw new Error('Session expired — please log in again');
  }
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${resp.status}`);
  }
  return resp.json();
}

/* ===== Toast ===== */
function toast(msg, type = 'ok') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `<span>${type === 'ok' ? '✓' : '✕'}</span> ${msg}`;
  $('toasts').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; }, 3200);
  setTimeout(() => el.remove(), 3600);
}

/* ===== Navigation ===== */
const sidebar = $('sidebar');
const overlay = $('sidebar-overlay');

$('hamburger')?.addEventListener('click', () => {
  sidebar.classList.toggle('open');
  overlay.classList.toggle('open');
});
overlay?.addEventListener('click', closeSidebar);
function closeSidebar() {
  sidebar.classList.remove('open');
  overlay.classList.remove('open');
}

document.querySelectorAll('.nav-item').forEach(btn => {
  btn.addEventListener('click', () => {
    switchView(btn.dataset.view);
    closeSidebar();
  });
});

document.querySelectorAll('[data-goto]').forEach(btn => {
  btn.addEventListener('click', () => {
    switchView(btn.dataset.goto);
    closeSidebar();
  });
});

// Hash-based routing for back/forward button support
function switchView(name, skipHash = false) {
  document.querySelectorAll('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.view === name));
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === `view-${name}`));
  if (!skipHash) {
    history.pushState({ view: name }, '', `#${name}`);
  }
  if (name === 'dashboard') { loadDashboard(); loadLive(); }
  if (name === 'sources') loadTasks();
  if (name === 'activity') loadVideos();
  if (name === 'youtube') loadYouTube();
  if (name === 'social') loadSocial();
  if (name === 'quick') { populateQuickDropdowns(); $('q-progress-card').style.display = 'none'; $('q-submit').disabled = false; $('q-submit').textContent = '⚡ Start Upload'; }
  if (name === 'manual') loadManualJobs();
}

// Handle browser back/forward
window.addEventListener('popstate', (e) => {
  const view = e.state?.view || location.hash.slice(1) || 'dashboard';
  switchView(view, true);
});

// On load: restore view from hash
function initRoute() {
  const view = location.hash.slice(1) || 'dashboard';
  const valid = ['dashboard', 'sources', 'activity', 'youtube', 'social', 'quick', 'manual'];
  switchView(valid.includes(view) ? view : 'dashboard', true);
}

/* ===== Clock ===== */
setInterval(() => {
  $('clock').textContent = new Date().toLocaleTimeString('en-GB');
}, 1000);

/* ===== Platform detection ===== */
function detectPlatformClient(url) {
  const u = url.toLowerCase();
  if (u.includes('youtube.com') || u.includes('youtu.be')) return 'youtube';
  if (u.includes('tiktok.com')) return 'tiktok';
  if (u.includes('instagram.com')) return 'instagram';
  if (u.includes('facebook.com') || u.includes('fb.com') || u.includes('fb.watch')) return 'facebook';
  return '';
}

const PLATFORM_META = {
  youtube:   { icon: '▶', label: 'YouTube',   cls: 'yt' },
  tiktok:    { icon: '♪', label: 'TikTok',     cls: 'tt' },
  instagram: { icon: '📷', label: 'Instagram',  cls: 'ig' },
  facebook:  { icon: 'f',  label: 'Facebook',   cls: 'fb' },
};

/* ===== Cookie auto-detect ===== */
function detectCookieFormat(text, filename) {
  const ext = (filename || '').split('.').pop().toLowerCase();

  // Try JSON first
  try {
    const parsed = JSON.parse(text);
    if (Array.isArray(parsed)) return { format: 'json', data: parsed };
    if (typeof parsed === 'object' && parsed !== null) {
      if (parsed.__format === 'pickle') return { format: 'pickle', data: parsed };
      return { format: 'json', data: parsed };
    }
  } catch (_) {}

  // Netscape (tab-separated lines)
  const lines = text.split('\n').filter(l => l.trim());
  const tabLines = lines.filter(l => l.split('\t').length >= 6);
  if (tabLines.length > 0) {
    return { format: 'netscape', data: text };
  }

  // Pickle file by extension
  if (ext === 'pkl') {
    return { format: 'pickle', data: text };
  }

  return { format: 'unknown', data: text };
}

/* ===== URL → detect platform + channel name ===== */
let detectTimer = null;

$('f-url')?.addEventListener('input', () => {
  const url = $('f-url').value.trim();
  clearTimeout(detectTimer);

  // Immediate client-side platform detect
  const plat = detectPlatformClient(url);
  const status = $('url-status');
  const hint = $('url-hint');

  if (!url) {
    status.textContent = '';
    status.className = 'input-status';
    $('name-group').style.display = 'none';
    $('platform-display').style.display = 'none';
    $('dest-group').style.display = 'none';
    return;
  }

  if (plat) {
    const meta = PLATFORM_META[plat];
    status.textContent = '✓';
    status.className = 'input-status ok';
    hint.textContent = `Detected: ${meta.label}`;
    hint.style.color = 'var(--green)';
    $('platform-display').style.display = '';
    $('detected-platform-badge').innerHTML = `<span class="platform-dot ${meta.cls}">${meta.icon}</span> ${meta.label}`;
    $('dest-group').style.display = '';
    populateAccountDropdowns();
  } else {
    status.textContent = '?';
    status.className = 'input-status err';
    hint.textContent = 'Unknown platform — paste a YouTube, TikTok, Instagram, or Facebook link';
    hint.style.color = 'var(--red)';
    return;
  }

  // Debounced server-side name detection
  if (url.startsWith('http')) {
    status.textContent = '…';
    status.className = 'input-status load';
    const isFbMedia = /facebook\.com\/(reel|watch|video|share)\//i.test(url);
    detectTimer = setTimeout(async () => {
      try {
        const d = await api(`/api/detect?url=${encodeURIComponent(url)}`);
        $('name-group').style.display = '';
        if (d.name) {
          $('f-name').value = d.name;
          $('f-name').placeholder = 'Auto-detected — edit if needed';
          document.querySelector('#name-group .form-hint').textContent = 'Auto-filled from the URL. Edit if needed.';
        } else if (isFbMedia) {
          $('f-name').placeholder = 'Page name (e.g. "MrBeast")';
          document.querySelector('#name-group .form-hint').textContent = 'Reel/video link — page name can\'t be auto-detected, type it manually (optional).';
        } else {
          $('f-name').placeholder = 'Enter channel name manually…';
          document.querySelector('#name-group .form-hint').textContent = 'Couldn\'t auto-detect — type it manually (optional).';
        }
        status.textContent = '✓';
        status.className = 'input-status ok';
      } catch (_) {
        status.textContent = '✓';
        status.className = 'input-status ok';
        $('name-group').style.display = '';
      }
    }, 800);
  }
});

/* ===== Account dropdowns ===== */
let allAccounts = [];

async function fillDestDropdowns(idPrefix, acctPrefix) {
  // idPrefix: 'f-' (Add Source) or 'q-' (Quick Upload)
  // acctPrefix: 'acct-' or 'q-acct-'
  const lists = { youtube: [] };
  try {
    const d = await api('/api/logins');
    lists.youtube = (d.logins || [])
      .filter(l => l.provider === 'youtube')
      .map(l => ({ id: l.id, username: l.channel_title || l.label || `Channel #${l.id}` }));
  } catch (_) { lists.youtube = []; }
  ['tiktok', 'instagram', 'facebook'].forEach(p => {
    lists[p] = allAccounts.filter(a => a.platform === p);
  });

  ['youtube', 'tiktok', 'instagram', 'facebook'].forEach(plat => {
    const cb = $(`${idPrefix}${plat}`);
    const sel = $(`${acctPrefix}${plat}`);
    if (!cb || !sel) return;

    const accounts = lists[plat];
    sel.innerHTML = '<option value="">Select account…</option>';
    accounts.forEach(a => {
      const opt = document.createElement('option');
      opt.value = a.id;
      opt.textContent = a.username || a.id;
      sel.appendChild(opt);
    });
    if (accounts.length === 0) {
      sel.innerHTML = '<option value="">No accounts connected</option>';
    }

    cb.onchange = () => {
      sel.disabled = !cb.checked;
      if (cb.checked && accounts.length === 1) sel.value = String(accounts[0].id);
    };
    // Apply current checkbox state immediately (YT starts checked)
    cb.onchange();
  });
}

async function populateAccountDropdowns() {
  try {
    const d = await api('/api/accounts');
    allAccounts = d.accounts || [];
  } catch (_) { allAccounts = []; }
  await fillDestDropdowns('f-', 'acct-');
}

/* ===== Add Source modal ===== */
function openModal() {
  $('modal-add').classList.add('open');
  $('f-url').value = '';
  $('f-name').value = '';
  $('url-status').textContent = '';
  $('url-hint').textContent = 'Paste a channel, playlist, or video link';
  $('url-hint').style.color = '';
  $('name-group').style.display = 'none';
  $('platform-display').style.display = 'none';
  $('dest-group').style.display = 'none';
  $('f-youtube').checked = true;
  ['tiktok', 'instagram', 'facebook'].forEach(p => {
    const cb = $(`f-${p}`);
    if (cb) { cb.checked = false; cb.onchange && cb.onchange(); }
  });
  populateAccountDropdowns();
  setTimeout(() => $('f-url').focus(), 100);
}

function closeModal() {
  $('modal-add').classList.remove('open');
}

$('btn-add-source')?.addEventListener('click', openModal);
$('btn-quick-add')?.addEventListener('click', openModal);
$('btn-empty-add')?.addEventListener('click', openModal);
$('modal-close')?.addEventListener('click', closeModal);
$('btn-cancel-add')?.addEventListener('click', closeModal);
$('modal-add')?.addEventListener('click', e => { if (e.target === $('modal-add')) closeModal(); });

/* ===== Submit Add Source ===== */
$('add-form')?.addEventListener('submit', async e => {
  e.preventDefault();
  const url = $('f-url').value.trim();
  const name = $('f-name').value.trim() || null;
  const platform = detectPlatformClient(url) || 'youtube';

  const dest = [];
  if ($('f-youtube')?.checked) {
    const ytAcct = $('acct-youtube')?.value;
    dest.push(ytAcct ? `youtube:${ytAcct}` : 'youtube');
  }
  ['tiktok', 'instagram', 'facebook'].forEach(p => {
    if ($(`f-${p}`)?.checked) {
      const acctId = $(`acct-${p}`)?.value;
      // Store as "platform" or "platform:account_id"
      dest.push(acctId ? `${p}:${acctId}` : p);
    }
  });
  if (dest.length === 0) return toast('Select at least one destination', 'err');

  try {
    await api('/api/tasks', {
      method: 'POST',
      body: JSON.stringify({ source_url: url, name, platform, dest_platforms: dest }),
    });
    toast('Source added successfully');
    closeModal();
    loadTasks();
    loadDashboard();
  } catch (e2) {
    toast(e2.message, 'err');
  }
});

/* ===== Load Tasks (Sources) ===== */
let ytLoginsForLabels = [];

async function loadTasks() {
  try {
    const [d, dl] = await Promise.all([
      api('/api/tasks'),
      api('/api/logins').catch(() => ({ logins: [] })),
    ]);
    ytLoginsForLabels = (dl.logins || []).filter(l => l.provider === 'youtube');
    const tasks = d.tasks || [];
    $('nav-task-count').textContent = tasks.length;

    const tbody = document.querySelector('#tasks-table tbody');
    const empty = $('tasks-empty');

    if (tasks.length === 0) {
      tbody.innerHTML = '';
      empty.style.display = '';
      document.querySelector('#tasks-table').style.display = 'none';
      return;
    }
    empty.style.display = 'none';
    document.querySelector('#tasks-table').style.display = '';

    tbody.innerHTML = tasks.map(t => {
      const meta = PLATFORM_META[t.platform] || PLATFORM_META.youtube;
      const destParts = (t.dest_platforms || 'youtube').split(',');
      const destBadges = destParts.map(dp => {
        const [plat, acctId] = dp.split(':');
        const m = PLATFORM_META[plat];
        if (!m) return '';
        let label = m.label;
        if (acctId) {
          if (plat === 'youtube') {
            const l = ytLoginsForLabels.find(x => String(x.id) === acctId);
            label = l ? (l.channel_title || `Channel #${acctId}`) : `Channel #${acctId}`;
          } else {
            const acct = allAccounts.find(a => String(a.id) === acctId);
            if (acct?.username) label += ` · ${acct.username}`;
          }
        }
        return `<span class="plat-pill ${m.cls}">${m.icon} ${esc(label)}</span>`;
      }).join(' ');

      const s = t.stats || {};
      const monitor = `
        <div class="job-monitor" onclick="openAnalytics(${t.id})" title="Click for full analytics">
          <span class="jm-num up" title="uploaded">${s.uploaded || 0}</span>
          <span class="jm-num" title="total">${s.total || 0}</span>
          <span class="jm-num ${s.failed ? 'down' : ''}" title="failed">${s.failed || 0}</span>
          ${s.live ? `<span class="badge run jm-live"><span class="badge-dot"></span> ${s.live} live</span>` : ''}
          ${s.retryable ? `<span class="badge warn" title="auto-retryable failures">↻ ${s.retryable}</span>` : ''}
          <span class="jm-cta">analytics →</span>
        </div>`;

      const isActive = t.status === 'active';
      const active = isActive ? `<span class="badge ok"><span class="badge-dot"></span> Active</span>` : `<span class="badge idle">Paused</span>`;
      const lastSeen = t.last_poll_at ? timeAgo(new Date(t.last_poll_at * 1000).toISOString()) : (t.last_seen_key ? 'seen' : '—');

      return `<tr>
        <td>
          <div class="cell-title">${esc(t.name || t.source_url)}</div>
          <div class="cell-sub">${esc(t.source_url)}</div>
        </td>
        <td><span class="plat-pill ${meta.cls}">${meta.icon} ${meta.label}</span></td>
        <td>${destBadges}</td>
        <td>${monitor}</td>
        <td>${active}</td>
        <td class="muted">${lastSeen}</td>
        <td>
          <button class="btn ghost sm" onclick="openAnalytics(${t.id})" title="Analytics">▤</button>
          <button class="btn ghost sm" onclick="toggleTask('${t.id}', '${isActive ? 'paused' : 'active'}')">${isActive ? '⏸' : '▶'}</button>
          <button class="btn danger sm" onclick="delTask('${t.id}')">✕</button>
        </td>
      </tr>`;
    }).join('');
  } catch (e) {
    console.error('loadTasks:', e);
  }
}

async function toggleTask(id, status) {
  try {
    await api(`/api/tasks/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) });
    loadTasks();
  } catch (e) { toast(e.message, 'err'); }
}

async function delTask(id) {
  if (!confirm('Remove this source?')) return;
  try {
    await api(`/api/tasks/${id}`, { method: 'DELETE' });
    toast('Source removed');
    loadTasks();
    loadDashboard();
  } catch (e) { toast(e.message, 'err'); }
}

/* ===== Load Videos (Activity) ===== */
let videoFilter = 'all';

document.querySelectorAll('.pill[data-filter]').forEach(p => {
  p.addEventListener('click', () => {
    document.querySelectorAll('.pill[data-filter]').forEach(x => x.classList.remove('active'));
    p.classList.add('active');
    videoFilter = p.dataset.filter;
    loadVideos();
  });
});

async function loadVideos() {
  try {
    const d = await api('/api/videos?limit=300');
    let videos = d.videos || [];
    $('nav-video-count').textContent = videos.length;

    if (videoFilter === 'live') {
      videos = videos.filter(v => LIVE_STATUSES.includes(v.status));
    } else if (videoFilter !== 'all') {
      videos = videos.filter(v => v.status === videoFilter);
    }

    const tbody = document.querySelector('#videos-table tbody');
    const empty = $('videos-empty');

    if (videos.length === 0) {
      tbody.innerHTML = '';
      empty.style.display = '';
      document.querySelector('#videos-table').style.display = 'none';
      return;
    }
    empty.style.display = 'none';
    document.querySelector('#videos-table').style.display = '';

    tbody.innerHTML = videos.map(v => {
      const badge = statusBadge(v.status);
      let ref = {};
      try { ref = v.upload_ref ? JSON.parse(v.upload_ref) : {}; } catch (_) {}
      const ytId = ref.id && ref.provider === 'youtube' ? ref.id : null;
      const ytLink = ytId ? `<a href="https://youtu.be/${esc(ytId)}" target="_blank">YouTube ↗</a>` : '';
      const retryBadge = (v.retry_count || 0) > 0
        ? `<span class="badge warn" title="auto/manual retries used">↻ ${v.retry_count}/3</span>` : '';
      const errTitle = ref.error || v.error;

      return `<tr>
        <td>
          <div class="cell-title">${esc(v.title || v.video_key)}</div>
          <div class="cell-sub">${ytLink || `<span class="muted">${esc(v.url || '').slice(0, 60)}</span>`}</div>
        </td>
        <td class="muted">${esc(v.source_name || '—')}</td>
        <td>${badge} ${retryBadge}</td>
        <td>${stageChips(v)}</td>
        <td class="muted">${timeAgo(v.updated_at)}</td>
        <td style="white-space:nowrap">
          ${errTitle ? `<span class="muted" title="${esc(errTitle)}">⚠</span>` : ''}
          ${videoActions(v)}
        </td>
      </tr>`;
    }).join('');
  } catch (e) {
    console.error('loadVideos:', e);
  }
}

const LIVE_STATUSES = ['detected', 'downloading', 'analyzing', 'voiceover', 'editing', 'metadata', 'uploading'];
const STAGE_META = {
  detected:         { icon: '⊕', label: 'Detected' },
  downloading:      { icon: '⬇', label: 'Downloading' },
  analyzing:        { icon: '🧠', label: 'Analyzing' },
  voiceover:        { icon: '🎙', label: 'Voiceover' },
  editing:          { icon: '✂', label: 'Editing' },
  metadata:         { icon: '✨', label: 'Metadata' },
  uploading:        { icon: '↑', label: 'Uploading' },
  uploaded:         { icon: '✓', label: 'Uploaded' },
  failed:           { icon: '✕', label: 'Failed' },
  cancelled:        { icon: '⊘', label: 'Cancelled' },
};

function statusBadge(s) {
  const m = STAGE_META[s];
  if (s === 'uploaded') return `<span class="badge ok"><span class="badge-dot"></span> Uploaded</span>`;
  if (s === 'failed') return `<span class="badge err"><span class="badge-dot"></span> Failed</span>`;
  if (s === 'cancelled') return `<span class="badge idle">⊘ Cancelled</span>`;
  if (LIVE_STATUSES.includes(s)) return `<span class="badge run"><span class="badge-dot"></span> ${m ? m.label : esc(s)}</span>`;
  return `<span class="badge idle">${esc(s)}</span>`;
}

// Stage chips row: detected ✓ → downloaded ✓ → edited ✓ → uploaded
function stageChips(v) {
  const steps = [
    { on: !!(v.detected_at || v.created_at), icon: '⊕', title: 'detected' },
    { on: !!v.downloaded_at, icon: '⬇', title: 'downloaded' },
    { on: !!v.edited_at, icon: '✂', title: 'edited' },
    { on: !!v.uploaded_at, icon: '✓', title: 'uploaded' },
  ];
  if (v.failed_at) steps.push({ on: true, icon: '✕', title: 'failed', bad: true });
  if (v.cancelled_at) steps.push({ on: true, icon: '⊘', title: 'cancelled', bad: true });
  return `<span class="stage-chips">${steps.map(s =>
    `<span class="stage-chip ${s.on ? 'on' : ''} ${s.bad ? 'bad' : ''}" title="${s.title}">${s.icon}</span>`
  ).join('')}</span>`;
}

function fmtClock(ts) {
  if (!ts) return '—';
  const dt = new Date(Number(ts));
  if (isNaN(dt.getTime())) return '—';
  const same = dt.toDateString() === new Date().toDateString();
  const hm = `${String(dt.getHours()).padStart(2, '0')}:${String(dt.getMinutes()).padStart(2, '0')}`;
  return same ? hm : `${dt.getDate()}/${dt.getMonth() + 1} ${hm}`;
}

// Cancel / Retry actions for a video row
function videoActions(v) {
  const btns = [];
  if (LIVE_STATUSES.includes(v.status)) {
    btns.push(`<button class="btn danger sm" onclick="cancelVideo(${v.id})" title="Cancel processing">⊘</button>`);
  }
  if (v.status === 'failed' || v.status === 'cancelled') {
    const over = (v.retry_count || 0) >= 3;
    btns.push(`<button class="btn ghost sm" onclick="retryVideo(${v.id}${over ? ', true' : ''})" title="Retry (attempt ${(v.retry_count || 0) + 1}/3)${over ? ' — force beyond limit' : ''}">↻ Retry</button>`);
  }
  return btns.join(' ');
}

async function cancelVideo(id) {
  if (!confirm('Cancel this video? Processing will stop.')) return;
  try {
    await api(`/api/videos/${id}/cancel`, { method: 'POST' });
    toast('Video cancelled');
    refreshActiveViews();
  } catch (e) { toast(e.message, 'err'); }
}

async function retryVideo(id, force) {
  try {
    const d = await api(`/api/videos/${id}/retry${force ? '?force=1' : ''}`, { method: 'POST' });
    if (d.dispatched) toast('Re-queued for processing');
    else toast('Re-queued' + (d.dispatch_error ? ` (dispatch pending: ${d.dispatch_error})` : ''), d.dispatched ? 'ok' : 'ok');
    refreshActiveViews();
  } catch (e) { toast(e.message, 'err'); }
}

function refreshActiveViews() {
  const active = document.querySelector('.view.active')?.id;
  if (active === 'view-dashboard') { loadDashboard(); loadLive(); }
  if (active === 'view-sources') loadTasks();
  if (active === 'view-activity') loadVideos();
  if ($('modal-analytics').classList.contains('open') && analyticsTaskId) loadJobAnalytics();
}

/* ===== Job Analytics modal ===== */
let analyticsTaskId = null;
let analyticsRange = 'week';
let analyticsTimer = null;

function openAnalytics(taskId) {
  analyticsTaskId = taskId;
  $('modal-analytics').classList.add('open');
  setAnalyticsRange(analyticsRange);
  loadJobAnalytics();
  clearInterval(analyticsTimer);
  analyticsTimer = setInterval(() => {
    if ($('modal-analytics').classList.contains('open')) loadJobAnalytics();
  }, 5000);
}

function closeAnalytics() {
  $('modal-analytics').classList.remove('open');
  clearInterval(analyticsTimer);
  analyticsTimer = null;
  analyticsTaskId = null;
}
$('an-close')?.addEventListener('click', closeAnalytics);
$('modal-analytics')?.addEventListener('click', e => { if (e.target === $('modal-analytics')) closeAnalytics(); });
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && $('modal-analytics')?.classList.contains('open')) closeAnalytics();
});

document.querySelectorAll('#an-ranges .pill').forEach(p => {
  p.addEventListener('click', () => setAnalyticsRange(p.dataset.range));
});

function setAnalyticsRange(r) {
  analyticsRange = r;
  document.querySelectorAll('#an-ranges .pill').forEach(p => p.classList.toggle('active', p.dataset.range === r));
  $('an-custom-row').style.display = r === 'custom' ? 'flex' : 'none';
  if (r !== 'custom') loadJobAnalytics();
}

$('an-apply-custom')?.addEventListener('click', () => loadJobAnalytics());

async function loadJobAnalytics() {
  if (!analyticsTaskId) return;
  try {
    let qs = `?range=${analyticsRange}`;
    if (analyticsRange === 'custom') {
      const from = $('an-from').value, to = $('an-to').value;
      if (!from || !to) return;
      qs += `&from=${from}&to=${to}`;
    }
    const d = await api(`/api/tasks/${analyticsTaskId}/analytics${qs}`);
    renderJobAnalytics(d);
  } catch (e) {
    toast(e.message, 'err');
    if (/not found|forbidden|404|403/i.test(e.message || '')) closeAnalytics();
  }
}

function renderJobAnalytics(d) {
  $('an-title').textContent = `Analytics — ${d.task?.name || d.task?.source_url || 'Job'}`;

  // ---- stat cards ----
  const t = d.totals || {};
  const at = d.all_time || {};
  const cards = [
    { k: 'Detected', v: t.detected, cls: '' },
    { k: 'Downloaded', v: t.downloaded, cls: '' },
    { k: 'Edited', v: t.edited, cls: '' },
    { k: 'Uploaded', v: t.uploaded, cls: 'up' },
    { k: 'Failed', v: t.failed, cls: t.failed ? 'down' : '' },
    { k: 'Cancelled', v: t.cancelled, cls: '' },
    { k: 'In progress', v: d.in_progress, cls: d.in_progress ? 'run' : '' },
    { k: 'Avg time', v: d.avg_minutes != null ? `${d.avg_minutes}m` : '—', cls: '' },
    { k: 'Retries', v: d.retries_total, cls: '' },
  ];
  $('an-stats').innerHTML = cards.map(c =>
    `<div class="an-stat ${c.cls}"><div class="an-stat-v">${c.v ?? 0}</div><div class="an-stat-k">${c.k}</div></div>`
  ).join('') +
    `<div class="an-stat alltime"><div class="an-stat-v">${at.uploaded ?? 0}<span class="an-stat-sub">/${at.total ?? 0}</span></div><div class="an-stat-k">All time uploaded</div></div>`;

  // ---- time series chart (pure CSS stacked bars) ----
  const series = (d.series || []).slice();
  const chart = $('an-chart');
  if (series.length === 0) {
    chart.innerHTML = '<div class="empty-state">No data in this range</div>';
  } else {
    const KEYS = [
      { k: 'detected', c: 'detected' },
      { k: 'downloaded', c: 'downloaded' },
      { k: 'uploaded', c: 'uploaded' },
      { k: 'failed', c: 'failed' },
    ];
    const max = Math.max(1, ...series.map(s => KEYS.reduce((n, x) => n + (s[x.k] || 0), 0)));
    const label = (s) => {
      const dt = new Date(s.t);
      if (d.bucket === 'hour') return `${String(dt.getHours()).padStart(2, '0')}:00`;
      if (d.bucket === 'month') return `${dt.toLocaleString('en', { month: 'short' })} ${String(dt.getFullYear()).slice(2)}`;
      return `${dt.getDate()}/${dt.getMonth() + 1}`;
    };
    const showLabels = series.length <= 40;
    chart.innerHTML = `<div class="an-bars">` + series.map(s => {
      const total = KEYS.reduce((n, x) => n + (s[x.k] || 0), 0);
      const segs = KEYS.filter(x => s[x.k] > 0).map(x =>
        `<span class="an-seg ${x.c}" style="flex:${s[x.k]}" title="${x.k}: ${s[x.k]}"></span>`).join('');
      const title = `${label(s)} — det ${s.detected} · dl ${s.downloaded} · up ${s.uploaded} · fail ${s.failed} · cx ${s.cancelled}`;
      return `<div class="an-col" title="${title}">
        <div class="an-stack" style="height:${Math.round(total / max * 100)}%">${segs}</div>
        ${showLabels ? `<div class="an-lab">${label(s)}</div>` : ''}
      </div>`;
    }).join('') + `</div>`;
  }

  // ---- live now ----
  const live = d.live || [];
  $('an-live-count').textContent = live.length ? `${live.length} running` : '';
  $('an-live-table').querySelector('tbody').innerHTML = live.map(v => `<tr>
    <td><div class="cell-title">${esc(v.title || v.video_key)}</div></td>
    <td>${statusBadge(v.status)}</td>
    <td>${stageChips(v)}</td>
    <td class="muted">${(v.retry_count || 0)}/3${(v.attempts || 0) > 1 ? ` (up ${v.attempts})` : ''}</td>
    <td class="muted">${fmtClock(v.updated_at)}</td>
    <td>${videoActions(v)}</td>
  </tr>`).join('');
  $('an-live-empty').style.display = live.length ? 'none' : '';

  // ---- recent table ----
  const recent = d.recent || [];
  $('an-recent-table').querySelector('tbody').innerHTML = recent.map(v => `<tr>
    <td><div class="cell-title">${esc(v.title || v.video_key)}</div>
        <div class="cell-sub">${ytLinkFrom(v)}</div></td>
    <td>${statusBadge(v.status)} ${(v.retry_count || 0) > 0 ? `<span class="badge warn">↻ ${v.retry_count}/3</span>` : ''}</td>
    <td class="muted">${(v.retry_count || 0)}/3</td>
    <td class="muted">${v.duration ? Math.round(v.duration) + 's' : '—'}</td>
    <td class="muted">${fmtClock(v.updated_at)}</td>
    <td style="white-space:nowrap">${videoActions(v)}</td>
  </tr>`).join('');
  $('an-recent-empty').style.display = recent.length ? 'none' : '';
}

function ytLinkFrom(v) {
  try {
    const ref = v.upload_ref ? JSON.parse(v.upload_ref) : {};
    if (ref.id && ref.provider === 'youtube') return `<a href="https://youtu.be/${esc(ref.id)}" target="_blank">YouTube ↗</a>`;
  } catch (_) {}
  return '';
}

/* ===== Dashboard live card ===== */
async function loadLive() {
  try {
    const d = await api('/api/videos?status=live&limit=50');
    const live = d.videos || [];
    const card = $('dash-live-card');
    const tbody = document.querySelector('#dash-live-table tbody');
    card.style.display = live.length ? '' : 'none';
    $('dash-live-empty').style.display = live.length ? 'none' : '';
    $('dash-live-count').textContent = live.length ? `${live.length} running` : '';
    if (!live.length) { tbody.innerHTML = ''; return; }
    tbody.innerHTML = live.map(v => `<tr>
      <td><div class="cell-title">${esc(v.title || v.video_key)}</div></td>
      <td class="muted">${esc(v.source_name || '—')}</td>
      <td>${statusBadge(v.status)}</td>
      <td>${stageChips(v)}</td>
      <td class="muted">${(v.retry_count || 0)}/3</td>
      <td>${videoActions(v)}</td>
    </tr>`).join('');
  } catch (e) {
    console.error('loadLive:', e);
  }
}

/* ===== Dashboard ===== */
async function loadDashboard() {
  try {
    const d = await api('/api/state');
    const tasks = d.tasks || [];
    const videos = d.videos || [];
    const accounts = d.accounts || [];

    const uploaded = videos.filter(v => v.status === 'uploaded').length;
    const failed = videos.filter(v => v.status === 'failed').length;

    $('stat-sources').textContent = tasks.filter(t => t.active !== 0).length;
    $('stat-videos').textContent = videos.length;
    $('stat-uploaded').textContent = uploaded;
    $('stat-failed').textContent = failed;
    $('stat-upload-rate').textContent = `${uploaded} done`;
    $('stat-fail-rate').textContent = failed > 0 ? `${failed} errors` : 'clean';

    // Recent activity feed
    const recent = videos.slice(0, 8);
    const feed = $('dash-activity');
    if (recent.length === 0) {
      feed.innerHTML = '<div class="empty-state">No activity yet</div>';
    } else {
      feed.innerHTML = recent.map(v => {
        const cls = v.status === 'uploaded' ? 'ok' : v.status === 'failed' ? 'err' : v.status === 'cancelled' ? 'idle' : 'run';
        const icon = v.status === 'uploaded' ? '✓' : v.status === 'failed' ? '✕' : v.status === 'cancelled' ? '⊘' : '⟳';
        const m = STAGE_META[v.status];
        return `<div class="feed-item">
          <div class="feed-icon ${cls}">${icon}</div>
          <div class="feed-text">
            <div class="feed-title">${esc(v.title || v.video_key)}</div>
            <div class="feed-meta">${m ? esc(m.label) : esc(v.status)} · ${timeAgo(v.updated_at)}</div>
          </div>
        </div>`;
      }).join('');
    }

    // Connected accounts mini
    const accEl = $('dash-accounts');
    if (accounts.length === 0) {
      accEl.innerHTML = '<div class="empty-state">No social accounts connected</div>';
    } else {
      accEl.innerHTML = accounts.map(a => {
        const m = PLATFORM_META[a.platform];
        return `<div class="account-mini">
          <span class="platform-dot ${m?.cls || ''}">${m?.icon || '?'}</span>
          <span class="account-mini-name">${esc(a.username || a.platform)}</span>
          <span class="account-mini-status">● connected</span>
        </div>`;
      }).join('');
    }

    // Sidebar dots
    $('nav-yt-status').className = 'nav-dot' + ((d.logins || []).length > 0 ? ' on' : '');
    $('nav-social-status').className = 'nav-dot' + (accounts.length > 0 ? ' on' : '');
    $('nav-task-count').textContent = tasks.length;
    $('nav-video-count').textContent = videos.length;
  } catch (e) {
    console.error('loadDashboard:', e);
  }
}

/* ===== YouTube Account ===== */
async function loadYouTube() {
  try {
    const d = await api('/api/state');
    const logins = d.logins || [];
    $('nav-yt-status').className = 'nav-dot' + (logins.length > 0 ? ' on' : '');

    if (logins.length > 0) {
      $('yt-channel-name').textContent = logins[0].channel_title || 'Connected channel';
      $('yt-channel-meta').textContent = `Connected since ${new Date(logins[0].created_at).toLocaleDateString()}`;
      $('btn-login').textContent = 'Reconnect';
    }

    const list = $('login-list');
    if (logins.length === 0) {
      list.innerHTML = '';
      return;
    }
    list.innerHTML = logins.map(l => `
      <div class="account-row">
        <span class="platform-dot yt">▶</span>
        <div>
          <div class="account-row-name">${esc(l.channel_title || 'Channel')}</div>
          <div class="account-row-email">${esc(l.label || l.provider)}</div>
        </div>
        <button class="btn ghost sm" onclick="copyRefresh('${l.id}')">Copy refresh token</button>
        <button class="btn danger sm" onclick="delLogin('${l.id}')">Remove</button>
      </div>
    `).join('');
  } catch (e) {
    console.error('loadYouTube:', e);
  }
}

$('btn-login')?.addEventListener('click', async () => {
  try {
    const d = await api('/api/youtube/login/start', {
      method: 'POST',
      body: JSON.stringify({}),
    });
    window.location.href = d.auth_url;
  } catch (e) { toast(e.message, 'err'); }
});

async function copyRefresh(id) {
  try {
    const d = await api(`/api/logins/${id}/refresh-token`);
    if (d.refresh_token) {
      await navigator.clipboard.writeText(d.refresh_token);
      toast('Refresh token copied');
    } else toast('No token stored', 'err');
  } catch (e) { toast(e.message, 'err'); }
}

async function delLogin(id) {
  if (!confirm('Remove this YouTube connection?')) return;
  try {
    await api(`/api/logins/${id}`, { method: 'DELETE' });
    toast('Removed');
    loadYouTube();
  } catch (e) { toast(e.message, 'err'); }
}

/* ===== Social Accounts ===== */
async function loadSocial() {
  const grid = $('social-grid');
  let accounts = [];
  try {
    const d = await api('/api/accounts');
    accounts = d.accounts || [];
  } catch (_) {}

  const platforms = ['tiktok', 'instagram', 'facebook'];
  grid.innerHTML = platforms.map(p => {
    const meta = PLATFORM_META[p];
    const accs = accounts.filter(a => a.platform === p);
    const connected = accs.length > 0;

    const accList = accs.map(a => `
      <div class="social-conn">
        <span class="social-conn-dot on"></span>
        <span>${esc(a.username || 'Connected')}</span>
        ${a.country ? `<span class="badge idle" title="Fingerprint country">${esc(a.country)}</span>` : ''}
        ${a.proxy ? `<span class="badge idle" title="${esc(a.proxy)}">proxy</span>` : ''}
        <button class="btn ghost sm" style="margin-left:auto" onclick="openUpdateCookies('${a.id}')" title="Update cookies without re-adding">⟳ Cookies</button>
        <button class="btn danger sm" onclick="delAccount('${a.id}')" title="Delete account">✕</button>
      </div>
    `).join('');

    return `<div class="social-card ${p}">
      <div class="social-card-header">
        <div class="social-card-icon">${meta.icon}</div>
        <div class="social-card-title">${meta.label}</div>
        <span class="badge ${connected ? 'ok' : 'idle'}" style="margin-left:auto">
          ${connected ? `<span class="badge-dot"></span> ${accs.length} connected` : 'Not connected'}
        </span>
      </div>
      <div class="social-card-body">
        ${connected ? accList : '<div class="muted">No accounts yet. Use the form below to connect.</div>'}
      </div>
      <div class="social-card-actions">
        <button class="btn ghost sm" onclick="document.getElementById('af-platform').value='${p}'; document.getElementById('af-cookies').click();">
          ${connected ? '+ Add another' : 'Connect'}
        </button>
      </div>
    </div>`;
  }).join('');

  loadAnalytics();
}

/* ===== Upload Analytics — per platform, per account ===== */
async function loadAnalytics() {
  const grid = $('analytics-grid');
  if (!grid) return;
  try {
    const [dj, ds] = await Promise.all([
      api('/api/manual/jobs'),
      api('/api/state'),
    ]);
    const jobs = dj.jobs || [];
    const accounts = ds.accounts || [];
    const logins = ds.logins || [];
    const videos = ds.videos || [];
    const accById = {};
    accounts.forEach(a => { accById[String(a.id)] = a; });

    // Upload events: {plat, acctKey, ok, url, ts}
    const events = [];
    const parse = s => {
      if (!s) return null;
      if (typeof s === 'object') return s;
      try { return JSON.parse(s); } catch { return null; }
    };

    jobs.forEach(j => {
      const res = parse(j.result);
      if (!res) return;
      (j.dest_platforms || '').split(',').forEach(d2 => {
        const d3 = d2.trim();
        if (!d3) return;
        const [plat, acctId] = d3.split(':');
        const r = res[plat];
        if (!PLATFORM_META[plat] || !r || typeof r !== 'object') return;
        events.push({
          plat,
          acctKey: acctId ? String(acctId) : 'default',
          ok: !!r.success,
          url: r.url || null,
          ts: r.uploaded_at || j.updated_at,
        });
      });
    });

    // Automation videos: upload_ref.social = {plat: {success,url,account}}
    videos.forEach(v => {
      const ref = parse(v.upload_ref);
      const soc = ref && ref.social;
      if (!soc || typeof soc !== 'object') return;
      Object.entries(soc).forEach(([plat, r]) => {
        if (!PLATFORM_META[plat] || !r || typeof r !== 'object') return;
        events.push({
          plat,
          acctKey: r.account ? String(r.account) : 'default',
          ok: !!r.success,
          url: r.url || null,
          ts: v.updated_at,
        });
      });
    });

    let html = '';
    ['youtube', 'tiktok', 'instagram', 'facebook'].forEach(plat => {
      const m = PLATFORM_META[plat];
      const evs = events.filter(e => e.plat === plat);
      const keys = new Set(evs.map(e => e.acctKey));
      if (plat !== 'youtube') {
        accounts.filter(a => a.platform === plat).forEach(a => keys.add(String(a.id)));
      }
      if (evs.length === 0 && keys.size === 0) return;

      const rows = [...keys].map(k => {
        const mine = evs.filter(e => e.acctKey === k);
        const okN = mine.filter(e => e.ok).length;
        const failN = mine.length - okN;
        const acct = accById[k];
        let name;
        if (plat === 'youtube') {
          name = logins[0]?.channel_title || 'YouTube channel';
        } else {
          name = acct ? (acct.username || `account #${k}`) : (k === 'default' ? 'Default account' : `#${k}`);
        }
        const badges = [
          acct?.country ? `<span class="badge idle" title="Fingerprint country">${esc(acct.country)}</span>` : '',
          acct?.proxy ? `<span class="badge idle" title="${esc(acct.proxy)}">proxy</span>` : '',
        ].join(' ');
        const last = mine.map(e => e.ts).filter(Boolean).sort((a, b) => b - a)[0];
        const links = mine.filter(e => e.ok && e.url).slice(0, 8).map(e =>
          `<a href="${esc(e.url)}" target="_blank" rel="noopener" class="plat-pill ${m.cls}">${m.icon} view</a>`
        ).join(' ');
        return `<div class="stat-row">
          <div class="stat-account">
            <span class="platform-dot ${m.cls}">${m.icon}</span>
            <b>${esc(name)}</b> ${badges}
          </div>
          <div class="stat-nums">
            <span class="stat-ok">${okN} success</span>
            <span class="stat-bad">${failN} failed</span>
            <span class="muted">last: ${last ? timeAgo(last) : '—'}</span>
          </div>
          <div class="stat-links">${links || '<span class="muted">no published links</span>'}</div>
        </div>`;
      }).join('');

      html += `<div class="stat-platform ${m.cls}">
        <div class="stat-platform-title">${m.icon} ${m.label}
          <span class="muted">· ${evs.length} upload${evs.length === 1 ? '' : 's'}</span>
        </div>
        ${rows || '<div class="muted">No accounts connected</div>'}
      </div>`;
    });

    grid.innerHTML = html || '<div class="empty-state">No upload data yet</div>';
  } catch (e) {
    console.error('loadAnalytics:', e);
    grid.innerHTML = '<div class="empty-state">Failed to load analytics</div>';
  }
}

async function delAccount(id) {
  if (!confirm('Remove this account?')) return;
  try {
    await api(`/api/accounts/${id}`, { method: 'DELETE' });
    toast('Account removed');
    loadSocial();
    loadDashboard();
  } catch (e) { toast(e.message, 'err'); }
}

/* ===== Connect Account form ===== */
async function parseCookiesFile(file) {
  const text = await file.text();
  const detected = detectCookieFormat(text, file.name);
  console.log(`[cookies] detected format: ${detected.format} for ${file.name}`);
  if (detected.format === 'json') return detected.data;
  if (detected.format === 'pickle') {
    const buf = await file.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let binary = '';
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return { __base64: btoa(binary), __format: 'pickle' };
  }
  return text; // Netscape raw string
}

$('af-cookies')?.addEventListener('change', () => {
  const file = $('af-cookies').files[0];
  const drop = $('file-drop');
  const txt = $('file-text');
  if (file) {
    drop.classList.add('has-file');
    txt.textContent = file.name;
  } else {
    drop.classList.remove('has-file');
    txt.textContent = 'Drop file or click to browse';
  }
});

$('account-form')?.addEventListener('submit', async e => {
  e.preventDefault();
  const platform = $('af-platform').value;
  const username = $('af-username').value.trim() || null;
  const proxy = $('af-proxy')?.value.trim() || '';
  const country = $('af-country')?.value || '';
  const file = $('af-cookies').files[0];
  if (!file) return toast('Select a cookies file', 'err');

  try {
    const cookies = await parseCookiesFile(file);

    await api('/api/accounts', {
      method: 'POST',
      body: JSON.stringify({ platform, username, cookies, proxy, country }),
    });

    $('af-cookies').value = '';
    $('af-username').value = '';
    if ($('af-proxy')) $('af-proxy').value = '';
    if ($('af-country')) $('af-country').value = '';
    $('file-drop').classList.remove('has-file');
    $('file-text').textContent = 'Drop file or click to browse';
    toast(`${PLATFORM_META[platform]?.label || platform} account connected`);
    loadSocial();
    loadDashboard();
  } catch (e2) {
    toast(e2.message, 'err');
  }
});

/* ===== Update Cookies modal ===== */
let updateCookiesId = null;

async function openUpdateCookies(id) {
  let account = null;
  try {
    const d = await api('/api/accounts');
    account = (d.accounts || []).find(a => String(a.id) === String(id));
  } catch (_) {}
  if (!account) return toast('Account not found', 'err');

  updateCookiesId = account.id;
  const meta = PLATFORM_META[account.platform] || { icon: '?', label: account.platform, cls: '' };
  const pill = $('uc-plat-pill');
  pill.className = `plat-pill ${meta.cls}`;
  pill.textContent = `${meta.icon} ${meta.label}`;
  $('uc-account-name').textContent = account.username || `account #${account.id}`;
  $('uf-username').value = account.username || '';
  $('uf-proxy').value = account.proxy || '';
  $('uf-country').value = account.country || '';
  $('uf-cookies').value = '';
  $('uc-file-drop').classList.remove('has-file');
  $('uc-file-text').textContent = 'Drop file or click to browse';
  $('modal-update-cookies').classList.add('open');
  setTimeout(() => $('uf-cookies').focus(), 100);
}

function closeUpdateCookies() {
  $('modal-update-cookies').classList.remove('open');
  updateCookiesId = null;
}
$('uc-close')?.addEventListener('click', closeUpdateCookies);
$('uc-cancel')?.addEventListener('click', closeUpdateCookies);
$('modal-update-cookies')?.addEventListener('click', e => { if (e.target === $('modal-update-cookies')) closeUpdateCookies(); });

$('uf-cookies')?.addEventListener('change', () => {
  const file = $('uf-cookies').files[0];
  if (file) {
    $('uc-file-drop').classList.add('has-file');
    $('uc-file-text').textContent = file.name;
  } else {
    $('uc-file-drop').classList.remove('has-file');
    $('uc-file-text').textContent = 'Drop file or click to browse';
  }
});

$('update-cookies-form')?.addEventListener('submit', async e => {
  e.preventDefault();
  if (!updateCookiesId) return;
  const file = $('uf-cookies').files[0];
  if (!file) return toast('Select a cookies file', 'err');

  try {
    const cookies = await parseCookiesFile(file);
    await api(`/api/accounts/${updateCookiesId}`, {
      method: 'PATCH',
      body: JSON.stringify({
        cookies,
        username: $('uf-username').value.trim() || undefined,
        proxy: $('uf-proxy')?.value.trim() || '',
        country: $('uf-country')?.value || '',
      }),
    });
    closeUpdateCookies();
    toast('Cookies updated');
    loadSocial();
    loadDashboard();
  } catch (e2) {
    toast(e2.message, 'err');
  }
});

/* ===== State / Health ===== */
async function loadState() {
  try {
    const d = await api('/api/state');
    const conn = $('conn');
    const txt = $('conn-text');
    conn.className = 'conn-dot on';
    txt.textContent = 'connected';
    const logins = d.logins || [];
    $('nav-yt-status').className = 'nav-dot' + (logins.length > 0 ? ' on' : '');
    const accounts = d.accounts || [];
    $('nav-social-status').className = 'nav-dot' + (accounts.length > 0 ? ' on' : '');
  } catch (e) {
    const conn = $('conn');
    conn.className = 'conn-dot off';
    $('conn-text').textContent = 'disconnected';
  }
}

/* ===== Quick Upload ===== */
let qPollTimer = null;

// Populate Quick Upload account dropdowns
async function populateQuickDropdowns() {
  try {
    const d = await api('/api/accounts');
    allAccounts = d.accounts || [];
  } catch (_) { allAccounts = []; }
  await fillDestDropdowns('q-', 'q-acct-');
}

// Quick Upload form submit
$('quick-form')?.addEventListener('submit', async e => {
  e.preventDefault();
  const url = $('q-url').value.trim();
  const title = $('q-title').value.trim() || null;

  const dest = [];
  if ($('q-youtube')?.checked) {
    const ytAcct = $('q-acct-youtube')?.value;
    dest.push(ytAcct ? `youtube:${ytAcct}` : 'youtube');
  }
  ['tiktok', 'instagram', 'facebook'].forEach(p => {
    if ($(`q-${p}`)?.checked) {
      const acctId = $(`q-acct-${p}`)?.value;
      dest.push(acctId ? `${p}:${acctId}` : p);
    }
  });

  if (dest.length === 0) return toast('Select at least one destination', 'err');

  try {
    $('q-submit').disabled = true;
    $('q-submit').textContent = '⏳ Submitting...';

    const d = await api('/api/manual/upload', {
      method: 'POST',
      body: JSON.stringify({ url, title, dest_platforms: dest }),
    });

    toast('Job queued! Processing will start shortly.');
    $('q-progress-card').style.display = '';
    updateProgressUI('queued');
    startQuickPoll(d.job.id);

    $('q-url').value = '';
    $('q-title').value = '';
  } catch (e2) {
    toast(e2.message, 'err');
    $('q-submit').disabled = false;
    $('q-submit').textContent = '⚡ Start Upload';
  }
});

function startQuickPoll(jobId) {
  clearInterval(qPollTimer);
  qPollTimer = setInterval(async () => {
    try {
      const d = await api('/api/manual/jobs');
      const job = (d.jobs || []).find(j => j.id === jobId);
      if (job) {
        updateProgressUI(job.status, job.error);
        if (['done', 'failed'].includes(job.status)) {
          clearInterval(qPollTimer);
          $('q-submit').disabled = false;
          $('q-submit').textContent = '⚡ Start Upload';
          if (job.status === 'done') toast('Upload complete!');
          if (job.status === 'failed') toast('Upload failed: ' + (job.error || 'unknown'), 'err');
        }
      }
    } catch (_) {}
  }, 3000);
}

function updateProgressUI(status, error) {
  const steps = document.querySelectorAll('#q-progress-steps .progress-step');
  const order = ['queued', 'downloading', 'editing', 'generating_metadata', 'uploading', 'done'];
  const currentIdx = order.indexOf(status);

  steps.forEach((el, i) => {
    el.classList.remove('active', 'done', 'failed');
    if (status === 'failed' && i === currentIdx) {
      el.classList.add('failed');
    } else if (i < currentIdx || (status === 'done' && i <= order.indexOf('done'))) {
      el.classList.add('done');
    } else if (i === currentIdx) {
      el.classList.add('active');
    }
  });

  const badge = $('q-status-badge');
  const text = $('q-status-text');
  if (badge && text) {
    text.textContent = status.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    badge.className = 'badge ' + (status === 'done' ? 'ok' : status === 'failed' ? 'err' : 'run');
    if (status === 'done') badge.innerHTML = '<span class="badge-dot"></span> Done';
    else if (status === 'failed') badge.innerHTML = '<span class="badge-dot"></span> Failed: ' + esc(error || '');
    else badge.innerHTML = `<span class="badge-dot"></span> ${text.textContent}`;
  }
}

// Load Manual Jobs list (client-side pagination: page numbers + size dropdown)
let manualPage = 1;
let manualPageSize = 25;

function manualGoto(p) {
  manualPage = p;
  loadManualJobs();
}

async function loadManualJobs() {
  try {
    const [d, da] = await Promise.all([
      api('/api/manual/jobs'),
      api('/api/accounts').catch(() => ({ accounts: [] })),
    ]);
    const jobs = d.jobs || [];
    const accounts = da.accounts || [];
    const accById = {};
    accounts.forEach(a => { accById[String(a.id)] = a; });

    $('nav-manual-count').textContent = jobs.filter(j => j.status === 'queued' || j.status === 'processing').length;

    // --- pagination ---
    manualPageSize = Number($('manual-pagesize')?.value) || 25;
    const total = jobs.length;
    const pages = Math.max(1, Math.ceil(total / manualPageSize));
    if (manualPage > pages) manualPage = pages;
    if (manualPage < 1) manualPage = 1;
    const pageJobs = jobs.slice((manualPage - 1) * manualPageSize, manualPage * manualPageSize);

    const tbody = document.querySelector('#manual-table tbody');
    const empty = $('manual-empty');
    const pager = $('manual-pager');

    if (total === 0) {
      tbody.innerHTML = '';
      empty.style.display = '';
      pager.style.display = 'none';
      document.querySelector('#manual-table').style.display = 'none';
      return;
    }
    empty.style.display = 'none';
    document.querySelector('#manual-table').style.display = '';

    // Pager controls: count + «page X/Y» + numbered buttons + prev/next
    pager.style.display = '';
    $('manual-count').textContent = `${total} job${total === 1 ? '' : 's'}`;
    const wanted = [];
    for (let p = 1; p <= pages; p++) {
      if (p === 1 || p === pages || Math.abs(p - manualPage) <= 2) wanted.push(p);
      else if (wanted[wanted.length - 1] !== '…') wanted.push('…');
    }
    $('manual-pagenums').innerHTML =
      `<button class="pg-btn" ${manualPage <= 1 ? 'disabled' : ''} onclick="manualGoto(${manualPage - 1})" title="Previous page">‹</button>` +
      wanted.map(p => p === '…'
        ? '<span class="pg-ellipsis">…</span>'
        : `<button class="pg-btn ${p === manualPage ? 'active' : ''}" onclick="manualGoto(${p})">${p}</button>`
      ).join('') +
      `<button class="pg-btn" ${manualPage >= pages ? 'disabled' : ''} onclick="manualGoto(${manualPage + 1})" title="Next page">›</button>`;

    const parseResult = r => {
      if (!r) return null;
      if (typeof r === 'object') return r;
      try { return JSON.parse(r); } catch { return null; }
    };
    const fmtTime = ts => {
      if (!ts) return '—';
      const dt = new Date(Number(ts));
      if (isNaN(dt.getTime())) return '—';
      const same = dt.toDateString() === new Date().toDateString();
      const hm = `${String(dt.getHours()).padStart(2, '0')}:${String(dt.getMinutes()).padStart(2, '0')}`;
      return same ? hm : `${dt.getDate()}/${dt.getMonth() + 1} ${hm}`;
    };
    const shortUrl = u => {
      try {
        const x = new URL(u);
        const s = (x.hostname.replace(/^www\./, '') + x.pathname).replace(/\/$/, '');
        return s.length > 48 ? s.slice(0, 47) + '…' : s;
      } catch { return String(u || '').slice(0, 48); }
    };

    tbody.innerHTML = pageJobs.map(j => {
      const result = parseResult(j.result);

      // Destinations with account names
      const dests = (j.dest_platforms || '').split(',').map(d2 => {
        const [plat, acctId] = d2.split(':');
        const m = PLATFORM_META[plat];
        if (!m) return '';
        const acc = acctId ? accById[acctId] : null;
        const who = acc ? (acc.username || `#${acctId}`) : (plat === 'youtube' ? 'YouTube' : m.label);
        return `<span class="plat-pill ${m.cls}">${m.icon} ${esc(who)}</span>`;
      }).join(' ');

      // Timing: downloaded → uploaded
      const dl = result?.downloaded_at;
      const ul = result?.uploaded_at;
      const timing = (dl || ul)
        ? `<div class="cell-sub">⬇ ${fmtTime(dl)} &nbsp;→&nbsp; ⬆ ${fmtTime(ul)}</div>`
        : `<span class="muted">—</span>`;

      // Published links per platform
      const links = [];
      if (result) {
        for (const [plat, r] of Object.entries(result)) {
          const m = PLATFORM_META[plat];
          if (!m || !r || typeof r !== 'object') continue;
          if (r.success && r.url) {
            links.push(`<a class="plat-pill ${m.cls}" href="${esc(r.url)}" target="_blank" rel="noopener" title="${esc(r.url)}">${m.icon} view</a>`);
          } else if (r.success) {
            links.push(`<span class="plat-pill ${m.cls}" title="uploaded (URL not captured)">${m.icon} ✓</span>`);
          } else if (r.error) {
            links.push(`<span class="plat-pill ${m.cls}" style="opacity:.55" title="${esc(r.error)}">${m.icon} ✕</span>`);
          }
        }
      }

      const badge = j.status === 'done' ? '<span class="badge ok"><span class="badge-dot"></span> Done</span>'
        : j.status === 'failed' ? `<span class="badge err" title="${esc(j.error)}"><span class="badge-dot"></span> Failed</span>`
        : ['processing', 'downloading', 'editing', 'uploading', 'generating_metadata'].includes(j.status)
        ? `<span class="badge run"><span class="badge-dot"></span> ${j.status.replace(/_/g,' ')}</span>`
        : '<span class="badge idle">Queued</span>';

      return `<tr>
        <td>
          <div class="cell-title">${esc(j.title || shortUrl(j.url))}</div>
          <div class="cell-sub"><a href="${esc(j.url)}" target="_blank" rel="noopener">${esc(shortUrl(j.url))}</a> · ${timeAgo(j.created_at)}</div>
        </td>
        <td>${dests || '—'}</td>
        <td>${badge}</td>
        <td>${timing}</td>
        <td>${links.join(' ') || '<span class="muted">—</span>'}</td>
        <td>
          ${['queued','failed'].includes(j.status) ? `<button class="btn danger sm" onclick="delManualJob(${j.id})">✕</button>` : ''}
        </td>
      </tr>`;
    }).join('');
  } catch (e) {
    console.error('loadManualJobs:', e);
  }
}

async function delManualJob(id) {
  if (!confirm('Delete this job?')) return;
  try {
    await api(`/api/manual/jobs/${id}`, { method: 'DELETE' });
    toast('Job deleted');
    loadManualJobs();
  } catch (e) { toast(e.message, 'err'); }
}

/* ===== Helpers ===== */
function esc(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function timeAgo(ts) {
  if (!ts) return '—';
  const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

/* ===== Auth (login / signup / logout) ===== */
const authScreen = $('auth-screen');

function showAuth() { if (authScreen) authScreen.style.display = 'flex'; }
function hideAuth() { if (authScreen) authScreen.style.display = 'none'; }
function setAuthError(msg) { const el = $('auth-error'); if (el) el.textContent = msg || ''; }

function authTab(which) {
  const isLogin = which === 'login';
  $('auth-tab-login')?.classList.toggle('active', isLogin);
  $('auth-tab-register')?.classList.toggle('active', !isLogin);
  const lf = $('auth-login-form'); if (lf) lf.style.display = isLogin ? '' : 'none';
  const rf = $('auth-register-form'); if (rf) rf.style.display = isLogin ? 'none' : '';
  setAuthError('');
}
$('auth-tab-login')?.addEventListener('click', () => authTab('login'));
$('auth-tab-register')?.addEventListener('click', () => authTab('register'));

function afterLogin(d) {
  localStorage.setItem('nova_session', d.token);
  hideAuth();
  if (appStarted) {
    toast(`Welcome back, ${d.user.username}!`);
    loadState();
    initRoute();
  } else {
    startApp(d.user);
  }
}

$('auth-login-form')?.addEventListener('submit', async e => {
  e.preventDefault();
  setAuthError('');
  const btn = $('au-login-btn');
  btn.disabled = true; btn.textContent = 'Logging in…';
  try {
    const d = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({
        identity: $('au-identity').value.trim(),
        password: $('au-login-pass').value,
      }),
    });
    afterLogin(d);
  } catch (e2) {
    setAuthError(e2.message);
  } finally {
    btn.disabled = false; btn.textContent = 'Log in';
  }
});

$('auth-register-form')?.addEventListener('submit', async e => {
  e.preventDefault();
  setAuthError('');
  const btn = $('au-register-btn');
  btn.disabled = true; btn.textContent = 'Creating…';
  try {
    const d = await api('/api/auth/register', {
      method: 'POST',
      body: JSON.stringify({
        username: $('au-user').value.trim(),
        email: $('au-email').value.trim(),
        password: $('au-register-pass').value,
      }),
    });
    toast(`Account created — welcome, ${d.user.username}!`);
    afterLogin(d);
  } catch (e2) {
    setAuthError(e2.message);
  } finally {
    btn.disabled = false; btn.textContent = 'Create account';
  }
});

$('btn-logout')?.addEventListener('click', async () => {
  try { await api('/api/auth/logout', { method: 'POST' }); } catch (_) {}
  localStorage.removeItem('nova_session');
  location.reload();
});

/* ===== Init ===== */
let appStarted = false;

async function startApp(user) {
  if (appStarted) return;
  appStarted = true;
  $('auth-username').textContent = user?.username ? `@${user.username}` : '';
  hideAuth();

  loadState();
  initRoute();

  // Handle OAuth callback
  const params = new URLSearchParams(window.location.search);
  if (params.get('yt') === 'connected') {
    toast('YouTube connected successfully!');
    window.history.replaceState({}, '', window.location.pathname);
    switchView('youtube');
  } else if (params.get('yt') === 'error') {
    toast('YouTube connection failed: ' + (params.get('msg') || 'unknown'), 'err');
    window.history.replaceState({}, '', window.location.pathname);
  }

  // Poll every 30s
  setInterval(() => {
    loadState();
    const active = document.querySelector('.view.active')?.id;
    if (active === 'view-dashboard') { loadDashboard(); loadLive(); }
    if (active === 'view-sources') loadTasks();
    if (active === 'view-activity') loadVideos();
    if (active === 'view-manual') loadManualJobs();
  }, 30000);
}

(async () => {
  const token = localStorage.getItem('nova_session');
  if (token) {
    try {
      const d = await api('/api/auth/me');
      await startApp(d.user);
      return;
    } catch (_) {
      localStorage.removeItem('nova_session');
    }
  }
  showAuth();
})();
