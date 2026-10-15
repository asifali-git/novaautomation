/* ===== NOVA — Frontend Logic ===== */

const WORKER = 'https://novaautomation.asifaliasmr05.workers.dev';
const $ = id => document.getElementById(id);

/* ===== API ===== */
async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json' };
  const resp = await fetch(WORKER + path, { ...opts, headers });
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
document.querySelectorAll('.nav-item').forEach(btn => {
  btn.addEventListener('click', () => switchView(btn.dataset.view));
});

document.querySelectorAll('[data-goto]').forEach(btn => {
  btn.addEventListener('click', () => switchView(btn.dataset.goto));
});

function switchView(name) {
  document.querySelectorAll('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.view === name));
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === `view-${name}`));
  if (name === 'dashboard') loadDashboard();
  if (name === 'sources') loadTasks();
  if (name === 'activity') loadVideos();
  if (name === 'youtube') loadYouTube();
  if (name === 'social') loadSocial();
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
    detectTimer = setTimeout(async () => {
      try {
        const d = await api(`/api/detect?url=${encodeURIComponent(url)}`);
        if (d.name) {
          $('f-name').value = d.name;
          $('name-group').style.display = '';
        } else {
          $('name-group').style.display = '';
          $('f-name').placeholder = 'Enter channel name manually…';
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

async function populateAccountDropdowns() {
  try {
    const d = await api('/api/accounts');
    allAccounts = d.accounts || [];
  } catch (_) { allAccounts = []; }

  ['tiktok', 'instagram', 'facebook'].forEach(plat => {
    const cb = $(`f-${plat}`);
    const sel = $(`acct-${plat}`);
    if (!cb || !sel) return;

    const accounts = allAccounts.filter(a => a.platform === plat);

    // Rebuild options
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

    // Toggle account select when checkbox changes
    cb.onchange = () => {
      sel.disabled = !cb.checked;
      if (cb.checked && accounts.length === 1) {
        sel.value = accounts[0].id;
      }
    };
  });
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

  const dest = ['youtube'];
  ['tiktok', 'instagram', 'facebook'].forEach(p => {
    if ($(`f-${p}`)?.checked) {
      const acctId = $(`acct-${p}`)?.value;
      // Store as "platform" or "platform:account_id"
      dest.push(acctId ? `${p}:${acctId}` : p);
    }
  });

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
async function loadTasks() {
  try {
    const d = await api('/api/tasks');
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
          const acct = allAccounts.find(a => String(a.id) === acctId);
          if (acct?.username) label += ` · ${acct.username}`;
        }
        return `<span class="plat-pill ${m.cls}">${m.icon} ${label}</span>`;
      }).join(' ');

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
        <td>${active}</td>
        <td class="muted">${lastSeen}</td>
        <td>
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
    const d = await api('/api/videos');
    let videos = d.videos || [];
    $('nav-video-count').textContent = videos.length;

    if (videoFilter !== 'all') {
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
      const ytLink = ref.youtube ? `<a href="${esc(ref.youtube)}" target="_blank">YouTube ↗</a>` : '';

      return `<tr>
        <td>
          <div class="cell-title">${esc(v.title || v.video_key)}</div>
          <div class="cell-sub">${ytLink}</div>
        </td>
        <td class="muted">${esc(v.source_name || '—')}</td>
        <td>${badge}</td>
        <td class="muted">${timeAgo(v.updated_at)}</td>
        <td>${(ref.error || v.error) ? `<span class="muted" title="${esc(ref.error || v.error)}">⚠</span>` : ''}</td>
      </tr>`;
    }).join('');
  } catch (e) {
    console.error('loadVideos:', e);
  }
}

function statusBadge(s) {
  if (s === 'uploaded') return `<span class="badge ok"><span class="badge-dot"></span> Uploaded</span>`;
  if (s === 'processing') return `<span class="badge run"><span class="badge-dot"></span> Processing</span>`;
  if (s === 'failed') return `<span class="badge err"><span class="badge-dot"></span> Failed</span>`;
  if (s === 'uploading') return `<span class="badge run"><span class="badge-dot"></span> Uploading</span>`;
  if (s === 'downloaded') return `<span class="badge run"><span class="badge-dot"></span> Downloaded</span>`;
  return `<span class="badge idle">${esc(s)}</span>`;
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
        const cls = v.status === 'uploaded' ? 'ok' : v.status === 'failed' ? 'err' : 'run';
        const icon = v.status === 'uploaded' ? '✓' : v.status === 'failed' ? '✕' : '⟳';
        return `<div class="feed-item">
          <div class="feed-icon ${cls}">${icon}</div>
          <div class="feed-text">
            <div class="feed-title">${esc(v.title || v.video_key)}</div>
            <div class="feed-meta">${esc(v.status)} · ${timeAgo(v.updated_at)}</div>
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
    const d = await api('/api/youtube/auth-url');
    window.location.href = d.url;
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
        <button class="btn danger sm" style="margin-left:auto" onclick="delAccount('${a.id}')">✕</button>
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
  const file = $('af-cookies').files[0];
  if (!file) return toast('Select a cookies file', 'err');

  try {
    const text = await file.text();
    const detected = detectCookieFormat(text, file.name);
    console.log(`[cookies] detected format: ${detected.format} for ${file.name}`);

    let cookies;
    if (detected.format === 'json') {
      cookies = detected.data;
    } else if (detected.format === 'pickle') {
      const buf = await file.arrayBuffer();
      const bytes = new Uint8Array(buf);
      let binary = '';
      for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
      cookies = { __base64: btoa(binary), __format: 'pickle' };
    } else {
      // Netscape raw string
      cookies = text;
    }

    await api('/api/accounts', {
      method: 'POST',
      body: JSON.stringify({ platform, username, cookies }),
    });

    $('af-cookies').value = '';
    $('af-username').value = '';
    $('file-drop').classList.remove('has-file');
    $('file-text').textContent = 'Drop file or click to browse';
    toast(`${PLATFORM_META[platform]?.label || platform} account connected`);
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

/* ===== Init ===== */
(async function init() {
  loadState();
  loadDashboard();

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
    if (active === 'view-dashboard') loadDashboard();
    if (active === 'view-sources') loadTasks();
    if (active === 'view-activity') loadVideos();
  }, 30000);
})();
