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
  if (name === 'dashboard') loadDashboard();
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

/* ===== Quick Upload ===== */
let qPollTimer = null;

// Populate Quick Upload account dropdowns
async function populateQuickDropdowns() {
  try {
    const d = await api('/api/accounts');
    allAccounts = d.accounts || [];
  } catch (_) { allAccounts = []; }

  ['tiktok', 'instagram', 'facebook'].forEach(plat => {
    const cb = $(`q-${plat}`);
    const sel = $(`q-acct-${plat}`);
    if (!cb || !sel) return;

    const accounts = allAccounts.filter(a => a.platform === plat);
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
      if (cb.checked && accounts.length === 1) sel.value = accounts[0].id;
    };
  });
}

// Quick Upload form submit
$('quick-form')?.addEventListener('submit', async e => {
  e.preventDefault();
  const url = $('q-url').value.trim();
  const title = $('q-title').value.trim() || null;

  const dest = [];
  if ($('q-youtube')?.checked) dest.push('youtube');
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

// Load Manual Jobs list
async function loadManualJobs() {
  try {
    const d = await api('/api/manual/jobs');
    const jobs = d.jobs || [];
    $('nav-manual-count').textContent = jobs.filter(j => j.status === 'queued' || j.status === 'processing').length;

    const tbody = document.querySelector('#manual-table tbody');
    const empty = $('manual-empty');

    if (jobs.length === 0) {
      tbody.innerHTML = '';
      empty.style.display = '';
      document.querySelector('#manual-table').style.display = 'none';
      return;
    }
    empty.style.display = 'none';
    document.querySelector('#manual-table').style.display = '';

    tbody.innerHTML = jobs.map(j => {
      const dests = (j.dest_platforms || '').split(',').map(d2 => {
        const [plat] = d2.split(':');
        const m = PLATFORM_META[plat];
        return m ? `<span class="plat-pill ${m.cls}">${m.icon} ${m.label}</span>` : '';
      }).join(' ');

      const badge = j.status === 'done' ? '<span class="badge ok"><span class="badge-dot"></span> Done</span>'
        : j.status === 'failed' ? `<span class="badge err" title="${esc(j.error)}"><span class="badge-dot"></span> Failed</span>`
        : j.status === 'processing' || j.status === 'downloading' || j.status === 'editing' || j.status === 'uploading' || j.status === 'generating_metadata'
        ? `<span class="badge run"><span class="badge-dot"></span> ${j.status.replace(/_/g,' ')}</span>`
        : '<span class="badge idle">Queued</span>';

      return `<tr>
        <td>
          <div class="cell-title">${esc(j.title || j.url)}</div>
          <div class="cell-sub">${esc(j.url)}</div>
        </td>
        <td>${esc(j.title || '—')}</td>
        <td>${dests}</td>
        <td>${badge}</td>
        <td class="muted">${timeAgo(j.created_at)}</td>
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

/* ===== Init ===== */
(async function init() {
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
    if (active === 'view-dashboard') loadDashboard();
    if (active === 'view-sources') loadTasks();
    if (active === 'view-activity') loadVideos();
    if (active === 'view-manual') loadManualJobs();
  }, 30000);
})();
