// Nova frontend logic. Talks to the worker API.
// >>> Set this to your worker URL after deploy <<<
const API = 'https://nova-hub.asubilla.workers.dev';

const state = { tasks: [], videos: [], logins: [], config: {} };
const REFRESH_MS = 6000;

const $ = (id) => document.getElementById(id);

function toast(msg, kind = '') {
  const box = $('toasts');
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), 3800);
}

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { 'content-type': 'application/json' },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function platformOf(url = '') {
  const u = url.toLowerCase();
  if (u.includes('youtube.com') || u.includes('youtu.be')) return 'youtube';
  if (u.includes('facebook.com') || u.includes('fb.watch') || u.includes('fb.com')) return 'facebook';
  if (u.includes('tiktok.com')) return 'tiktok';
  if (u.includes('instagram.com')) return 'instagram';
  if (u.includes('twitter.com') || u.includes('x.com')) return 'twitter';
  return 'link';
}

function timeAgo(ts) {
  if (!ts) return '—';
  const s = Math.max(0, (Date.now() - ts) / 1000);
  if (s < 60) return Math.floor(s) + 's ago';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}

// --- render -----------------------------------------------------------------

function renderLogins() {
  const box = $('login-list');
  const sel = $('f-login');
  box.innerHTML = '';
  sel.innerHTML = '';
  if (!state.logins.length) {
    box.innerHTML = '<div class="no-login">No account connected yet.</div>';
    sel.innerHTML = '<option value="">(none yet)</option>';
    return;
  }
  for (const l of state.logins) {
    const row = document.createElement('div');
    row.className = 'login-row';
    row.innerHTML = `
      <span class="who">${esc(l.channel_title || l.label || 'Account #' + l.id)}</span>
      <span class="acts">
        <button class="btn sm ghost" data-copy="${l.id}">Copy RT</button>
        <button class="btn sm danger" data-del-login="${l.id}">Remove</button>
      </span>`;
    box.appendChild(row);

    const opt = document.createElement('option');
    opt.value = l.id;
    opt.textContent = l.channel_title || l.label || 'Account #' + l.id;
    sel.appendChild(opt);
  }
  box.querySelectorAll('[data-copy]').forEach((b) =>
    b.onclick = () => copyRefresh(b.dataset.copy));
  box.querySelectorAll('[data-del-login]').forEach((b) =>
    b.onclick = () => delLogin(b.dataset.delLogin));
}

function renderTasks() {
  const tbody = $('tasks-table').querySelector('tbody');
  tbody.innerHTML = '';
  $('task-count').textContent = state.tasks.length;
  $('tasks-empty').style.display = state.tasks.length ? 'none' : 'block';
  for (const t of state.tasks) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="title-cell">${esc(t.name || t.source_url)}</td>
      <td><span class="platform">${esc(t.platform)}</span></td>
      <td><span class="badge ${esc(t.status)}">${esc(t.status)}</span></td>
      <td>${timeAgo(t.last_poll_at)}</td>
      <td class="row-acts">
        <button class="btn sm ${t.status === 'active' ? 'ghost' : 'ok'}" data-toggle="${t.id}">
          ${t.status === 'active' ? 'Pause' : 'Resume'}
        </button>
        <button class="btn sm danger" data-del="${t.id}">Delete</button>
      </td>`;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll('[data-toggle]').forEach((b) =>
    b.onclick = () => toggleTask(b.dataset.toggle));
  tbody.querySelectorAll('[data-del]').forEach((b) =>
    b.onclick = () => delTask(b.dataset.del));
}

function renderVideos() {
  const tbody = $('videos-table').querySelector('tbody');
  tbody.innerHTML = '';
  $('video-count').textContent = state.videos.length;
  $('videos-empty').style.display = state.videos.length ? 'none' : 'block';
  for (const v of state.videos) {
    let link = v.url;
    try {
      if (v.upload_ref) {
        const ref = JSON.parse(v.upload_ref);
        if (ref.id) link = 'https://youtu.be/' + ref.id;
      }
    } catch {}
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="title-cell"><a href="${esc(link)}" target="_blank" rel="noopener">${esc(v.title || v.video_key)}</a></td>
      <td><span class="platform">${esc(platformOf(v.url))}</span></td>
      <td><span class="badge ${esc(v.status)}">${esc(v.status)}</span></td>
      <td>${timeAgo(v.updated_at)}</td>
      <td class="row-acts">${v.error ? `<button class="btn sm ghost" title="${esc(v.error)}">Why?</button>` : ''}</td>`;
    tbody.appendChild(tr);
  }
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// --- actions ----------------------------------------------------------------

async function load() {
  try {
    const data = await api('/api/state');
    state.tasks = data.tasks || [];
    state.videos = data.videos || [];
    state.logins = data.logins || [];
    state.config = data.config || {};
    renderLogins();
    renderTasks();
    renderVideos();
    setConn(true);
  } catch (e) {
    setConn(false);
  }
}

function setConn(ok) {
  const el = $('conn');
  el.className = 'conn ' + (ok ? 'online' : 'offline');
  el.textContent = ok ? 'live' : 'offline';
}

async function addTask(e) {
  e.preventDefault();
  const url = $('f-url').value.trim();
  const name = $('f-name').value.trim();
  const loginId = $('f-login').value || null;
  if (!url) return;
  try {
    await api('/api/tasks', { method: 'POST', body: { source_url: url, name: name || undefined, login_id: loginId ? Number(loginId) : null } });
    $('f-url').value = '';
    $('f-name').value = '';
    toast('Source added', 'ok');
    load();
  } catch (e2) { toast(e2.message, 'err'); }
}

async function toggleTask(id) {
  const t = state.tasks.find((x) => String(x.id) === String(id));
  const next = t.status === 'active' ? 'paused' : 'active';
  try {
    await api(`/api/tasks/${id}`, { method: 'PATCH', body: { status: next } });
    toast('Source ' + next, 'ok');
    load();
  } catch (e) { toast(e.message, 'err'); }
}

async function delTask(id) {
  if (!confirm('Delete this source and its activity?')) return;
  try {
    await api(`/api/tasks/${id}`, { method: 'DELETE' });
    toast('Source deleted', 'ok');
    load();
  } catch (e) { toast(e.message, 'err'); }
}

async function delLogin(id) {
  if (!confirm('Remove this connected account?')) return;
  try {
    await api(`/api/logins/${id}`, { method: 'DELETE' });
    toast('Account removed', 'ok');
    load();
  } catch (e) { toast(e.message, 'err'); }
}

async function copyRefresh(id) {
  try {
    const d = await api(`/api/logins/${id}/refresh-token`);
    if (!d.refresh_token) return toast('No token stored', 'err');
    await navigator.clipboard.writeText(d.refresh_token);
    toast('Refresh token copied', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

let popup = null;
let pollTimer = null;

async function startLogin() {
  try {
    const before = state.logins.length;
    const d = await api('/api/youtube/login/start', { method: 'POST', body: {} });
    popup = window.open(d.auth_url, 'nova_login', 'width=520,height=680');
    toast('Finish sign-in in the new window');
    // Poll for a new login to appear.
    clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      await load();
      if (state.logins.length > before) {
        clearInterval(pollTimer);
        if (popup && !popup.closed) popup.close();
        toast('Account connected', 'ok');
      }
    }, 2000);
    // Stop polling after 3 min.
    setTimeout(() => clearInterval(pollTimer), 180000);
  } catch (e) { toast(e.message, 'err'); }
}

// --- boot -------------------------------------------------------------------

function clock() {
  $('clock').textContent = new Date().toLocaleTimeString();
}

document.addEventListener('DOMContentLoaded', () => {
  $('add-form').addEventListener('submit', addTask);
  $('btn-login').addEventListener('click', startLogin);
  load();
  setInterval(load, REFRESH_MS);
  clock();
  setInterval(clock, 1000);
});
