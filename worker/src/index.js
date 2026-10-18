// nova-hub worker — single-file fetch router, no framework.
// Public API (frontend) + internal API (CI runner) + OAuth callback.

const JSON_HEADERS = { 'content-type': 'application/json; charset=utf-8' };

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}

function now() {
  return Date.now();
}

function detectPlatform(url = '') {
  const u = url.toLowerCase();
  if (u.includes('youtube.com') || u.includes('youtu.be')) return 'youtube';
  if (u.includes('facebook.com') || u.includes('fb.watch') || u.includes('fb.com')) return 'facebook';
  if (u.includes('tiktok.com')) return 'tiktok';
  if (u.includes('instagram.com')) return 'instagram';
  if (u.includes('twitter.com') || u.includes('x.com')) return 'twitter';
  return 'unknown';
}

function parseCookies(header = '') {
  const out = {};
  header.split(';').forEach((part) => {
    const idx = part.indexOf('=');
    if (idx > 0) out[part.slice(0, idx).trim()] = part.slice(idx + 1).trim();
  });
  return out;
}

function corsHeaders(origin, env) {
  const allowed = env.ALLOWED_ORIGIN || '';
  const ok = origin && (allowed === '*' || origin === allowed || origin.endsWith('.pages.dev'));
  return {
    'access-control-allow-origin': ok ? origin : (allowed && allowed !== '*' ? allowed : '*'),
    'access-control-allow-methods': 'GET,POST,PATCH,DELETE,OPTIONS',
    'access-control-allow-headers': 'content-type,authorization,x-token',
    'access-control-max-age': '86400',
  };
}

async function readBody(req) {
  try {
    return await req.json();
  } catch {
    return {};
  }
}

// --- DB helpers -------------------------------------------------------------

async function listTasks(db, user) {
  const res = user
    ? await db
        .prepare('SELECT * FROM tasks WHERE (? = 1 OR user_id = ?) ORDER BY id DESC')
        .bind(user.is_admin ? 1 : 0, user.id)
        .all()
    : await db.prepare('SELECT * FROM tasks ORDER BY id DESC').all();
  const tasks = res.results || [];
  // Per-task counters (total / uploaded / failed / live) for monitor cards.
  try {
    const scope = user ? ' AND (? = 1 OR user_id = ?)' : '';
    const vals = user ? [user.is_admin ? 1 : 0, user.id] : [];
    const sres = await db
      .prepare(
        `SELECT task_id, COUNT(*) AS total,
                SUM(CASE WHEN status = 'uploaded' THEN 1 ELSE 0 END) AS uploaded,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
                SUM(CASE WHEN status IN ('detected','downloading','analyzing','voiceover','editing','metadata','uploading') THEN 1 ELSE 0 END) AS live,
                SUM(CASE WHEN status = 'failed' AND retry_count < 3 THEN 1 ELSE 0 END) AS retryable,
                MAX(updated_at) AS last_activity
         FROM videos WHERE 1=1${scope} GROUP BY task_id`
      )
      .bind(...vals)
      .all();
    const map = {};
    (sres.results || []).forEach((r) => { map[r.task_id] = r; });
    tasks.forEach((t) => {
      const s = map[t.id];
      t.stats = s
        ? { total: +s.total || 0, uploaded: +s.uploaded || 0, failed: +s.failed || 0, cancelled: +s.cancelled || 0, live: +s.live || 0, retryable: +s.retryable || 0, last_activity: s.last_activity || null }
        : { total: 0, uploaded: 0, failed: 0, cancelled: 0, live: 0, retryable: 0, last_activity: null };
    });
  } catch { /* stats are best-effort */ }
  return tasks;
}

async function getTask(db, id) {
  return db.prepare('SELECT * FROM tasks WHERE id = ?').bind(id).first();
}

async function createTask(db, body) {
  const url = (body.source_url || '').trim();
  if (!url) throw new Error('source_url required');
  const platform = body.platform || detectPlatform(url);
  const dest = body.dest_provider || 'youtube';
  const destPlatforms = body.dest_platforms ? (Array.isArray(body.dest_platforms) ? body.dest_platforms.join(',') : String(body.dest_platforms)) : null;
  const loginId = body.login_id || null;
  const userId = body.user_id || 0;
  const res = await db
    .prepare(
      `INSERT INTO tasks (name, source_url, platform, dest_provider, dest_platforms, login_id, user_id, status, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)`
    )
    .bind(body.name || platform, url, platform, dest, destPlatforms, loginId, userId, now())
    .run();
  return getTask(db, res.meta?.last_row_id);
}

async function patchTask(db, id, body) {
  const task = await getTask(db, id);
  if (!task) return null;
  const fields = [];
  const vals = [];
  for (const k of ['name', 'status', 'dest_provider', 'login_id', 'baseline_key', 'last_seen_key']) {
    if (k in body) {
      fields.push(`${k} = ?`);
      vals.push(body[k]);
    }
  }
  if ('dest_platforms' in body) {
    fields.push('dest_platforms = ?');
    const dp = body.dest_platforms;
    vals.push(dp == null ? null : (Array.isArray(dp) ? dp.join(',') : String(dp)));
  }
  if (!fields.length) return task;
  vals.push(id);
  await db.prepare(`UPDATE tasks SET ${fields.join(', ')} WHERE id = ?`).bind(...vals).run();
  return getTask(db, id);
}

async function deleteTask(db, id) {
  await db.prepare('DELETE FROM videos WHERE task_id = ?').bind(id).run();
  const res = await db.prepare('DELETE FROM tasks WHERE id = ?').bind(id).run();
  return res.meta?.changes > 0;
}

async function listVideos(db, { status, limit = 100, user } = {}) {
  const scope = user ? ` AND (? = 1 OR v.user_id = ?)` : '';
  const scopeVals = user ? [user.is_admin ? 1 : 0, user.id] : [];
  if (status) {
    const res = await db
      .prepare(
        `SELECT v.*, t.name AS source_name, t.source_url AS source
         FROM videos v LEFT JOIN tasks t ON v.task_id = t.id
         WHERE v.status = ?${scope} ORDER BY v.id DESC LIMIT ?`
      )
      .bind(status, ...scopeVals, limit)
      .all();
    return res.results || [];
  }
  const res = await db
    .prepare(
      `SELECT v.*, t.name AS source_name, t.source_url AS source
       FROM videos v LEFT JOIN tasks t ON v.task_id = t.id
       WHERE 1=1${scope} ORDER BY v.id DESC LIMIT ?`
    )
    .bind(...scopeVals, limit)
    .all();
  return res.results || [];
}

async function getVideo(db, id) {
  return db.prepare('SELECT * FROM videos WHERE id = ?').bind(id).first();
}

async function addVideo(db, body) {
  const taskId = body.task_id;
  const key = body.video_key;
  const url = body.url;
  if (!taskId || !key || !url) throw new Error('task_id, video_key, url required');
  const existing = await db
    .prepare('SELECT * FROM videos WHERE task_id = ? AND video_key = ?')
    .bind(taskId, key)
    .first();
  if (existing) return { video: existing, created: false };
  // Inherit ownership from the task so video lists are per-user.
  let userId = body.user_id;
  if (userId == null) {
    const trow = await db.prepare('SELECT user_id FROM tasks WHERE id = ?').bind(taskId).first();
    userId = trow?.user_id || 0;
  }
  const t = now();
  const res = await db
    .prepare(
      `INSERT INTO videos (task_id, video_key, url, title, status, user_id, detected_at, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'detected', ?, ?, ?, ?)`
    )
    .bind(taskId, key, url, body.title || null, userId, t, t, t)
    .run();
  return { video: await getVideo(db, res.meta?.last_row_id), created: true };
}

const VIDEO_FIELDS = [
  'status', 'error', 'meta', 'upload_ref', 'file_path', 'processed_path', 'attempts', 'duration', 'title',
  'retry_count', 'detected_at', 'downloaded_at', 'edited_at', 'uploaded_at', 'failed_at', 'cancelled_at',
];

// Statuses that mean the video is still being worked on.
const LIVE_STATUSES = ['detected', 'downloading', 'analyzing', 'voiceover', 'editing', 'metadata', 'uploading'];
const TERMINAL_STATUSES = ['uploaded', 'failed', 'cancelled'];
// Which stage-timestamp column each settled status implies.
const STATUS_STAMP = {
  uploaded: 'uploaded_at',
  failed: 'failed_at',
  cancelled: 'cancelled_at',
};
// Statuses that imply the download has finished (download completed before these).
const DOWNLOADED_BY = ['editing', 'analyzing', 'voiceover', 'metadata', 'uploading', 'uploaded'];
// Statuses that imply the edit/apply has finished. ('failed' excluded: it can
// happen at any stage — prior transitions already stamped these.)
const EDITED_BY = ['metadata', 'uploading', 'uploaded'];

async function patchVideo(db, id, body) {
  const video = await getVideo(db, id);
  if (!video) throw new Error('video not found');
  // Cancelled is sticky: a still-running pipeline can't un-cancel it.
  if (video.status === 'cancelled' && body.status && body.status !== 'cancelled') {
    delete body.status;
  }
  const fields = [];
  const vals = [];
  for (const k of VIDEO_FIELDS) {
    if (k in body) {
      fields.push(`${k} = ?`);
      vals.push(typeof body[k] === 'object' ? JSON.stringify(body[k]) : body[k]);
    }
  }
  // Auto stage timestamps on status transitions.
  const st = 'status' in body ? body.status : null;
  if (st) {
    const t = now();
    const stamp = (col) => {
      if (col in body || video[col]) return;
      fields.push(`${col} = ?`);
      vals.push(t);
    };
    if (DOWNLOADED_BY.includes(st)) stamp('downloaded_at');
    if (EDITED_BY.includes(st)) stamp('edited_at');
    if (STATUS_STAMP[st]) stamp(STATUS_STAMP[st]);
  }
  if (!fields.length) return video;
  fields.push('updated_at = ?');
  vals.push(now());
  vals.push(id);
  await db.prepare(`UPDATE videos SET ${fields.join(', ')} WHERE id = ?`).bind(...vals).run();
  return getVideo(db, id);
}

async function nextPendingVideo(db) {
  const res = await db
    .prepare(`SELECT * FROM videos WHERE status = 'detected' ORDER BY id ASC LIMIT 1`)
    .first();
  return res;
}

// --- Analytics ---------------------------------------------------------------

const DAY_MS = 86400000;

function analyticsWindow(range, fromParam, toParam) {
  const nowMs = now();
  switch (range) {
    case 'day':   return { from: nowMs - DAY_MS, to: nowMs, bucket: 'hour' };
    case 'week':  return { from: nowMs - 7 * DAY_MS, to: nowMs, bucket: 'day' };
    case 'month': return { from: nowMs - 30 * DAY_MS, to: nowMs, bucket: 'day' };
    case 'year':  return { from: nowMs - 365 * DAY_MS, to: nowMs, bucket: 'month' };
    case 'custom': {
      const f = Number(fromParam) || nowMs - 7 * DAY_MS;
      const t = Number(toParam) || nowMs;
      const span = Math.max(t - f, 3600000);
      return { from: f, to: Math.max(t, f + 3600000), bucket: span <= 2 * DAY_MS ? 'hour' : span <= 90 * DAY_MS ? 'day' : 'month' };
    }
    default: return { from: nowMs - 7 * DAY_MS, to: nowMs, bucket: 'day' };
  }
}

function bucketStart(ts, bucket) {
  const d = new Date(ts);
  if (bucket === 'hour') return new Date(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), d.getUTCHours()).getTime();
  if (bucket === 'month') return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
}

const EVENT_FIELDS = [
  ['detected', 'detected_at'],
  ['downloaded', 'downloaded_at'],
  ['edited', 'edited_at'],
  ['uploaded', 'uploaded_at'],
  ['failed', 'failed_at'],
  ['cancelled', 'cancelled_at'],
];

async function taskAnalytics(db, { taskId, user, range = 'week', from, to }) {
  const win = analyticsWindow(range, from, to);

  const task = await getTask(db, taskId);
  if (!task) return null;
  if (user && !user.is_admin && task.user_id !== user.id) return { forbidden: true };

  // Rows having ANY event inside the window (video may be older than window).
  const bind = [taskId];
  let evWhere = '';
  EVENT_FIELDS.forEach(([, col], i) => {
    if (i) evWhere += ' OR ';
    if (col === 'detected_at') {
      evWhere += '(COALESCE(detected_at, created_at) >= ? AND COALESCE(detected_at, created_at) < ?)';
    } else {
      evWhere += `(${col} >= ? AND ${col} < ?)`;
    }
    bind.push(win.from, win.to);
  });
  const rows = await db
    .prepare(
      `SELECT id, video_key, url, title, status, retry_count, attempts, error, duration, upload_ref,
              detected_at, downloaded_at, edited_at, uploaded_at, failed_at, cancelled_at,
              created_at, updated_at
       FROM videos WHERE task_id = ? AND (${evWhere})
       ORDER BY id DESC LIMIT 5000`
    )
    .bind(...bind)
    .all();
  const vids = rows.results || [];

  // Bucketed time series.
  const series = [];
  const index = {};
  const pushBucket = (t) => {
    if (index[t] != null) return index[t];
    index[t] = series.length;
    series.push({ t, detected: 0, downloaded: 0, edited: 0, uploaded: 0, failed: 0, cancelled: 0 });
    return series.length - 1;
  };
  const inWin = (ts) => ts != null && ts >= win.from && ts < win.to;
  const detTs = (v) => v.detected_at || v.created_at;
  const totals = { detected: 0, downloaded: 0, edited: 0, uploaded: 0, failed: 0, cancelled: 0 };
  const bump = (field, ts) => {
    if (!inWin(ts)) return;
    totals[field]++;
    series[pushBucket(bucketStart(ts, win.bucket))][field]++;
  };
  vids.forEach((v) => {
    bump('detected', detTs(v));
    bump('downloaded', v.downloaded_at);
    bump('edited', v.edited_at);
    bump('uploaded', v.uploaded_at);
    bump('failed', v.failed_at);
    bump('cancelled', v.cancelled_at);
  });

  // Fill empty buckets across the window so charts have continuous x-axis.
  const stepMs = win.bucket === 'hour' ? 3600000 : win.bucket === 'month' ? null : DAY_MS;
  if (stepMs) {
    const first = bucketStart(win.from, win.bucket);
    for (let t = first; t < win.to; t += stepMs) pushBucket(t);
  } else {
    const d0 = new Date(win.from);
    let y = d0.getUTCFullYear(), m = d0.getUTCMonth();
    const end = new Date(win.to);
    while (y < end.getUTCFullYear() || (y === end.getUTCFullYear() && m <= end.getUTCMonth())) {
      pushBucket(Date.UTC(y, m, 1));
      m++; if (m > 11) { m = 0; y++; }
    }
  }
  series.sort((a, b) => a.t - b.t);

  // Average detect -> upload time for videos uploaded in window.
  let sumMs = 0, nMs = 0;
  vids.forEach((v) => {
    if (v.uploaded_at && inWin(v.uploaded_at)) {
      const start = detTs(v);
      if (start && v.uploaded_at >= start) { sumMs += v.uploaded_at - start; nMs++; }
    }
  });

  // Live now + current status breakdown + retries (whole task, not just window).
  const all = await db
    .prepare(
      `SELECT id, video_key, url, title, status, retry_count, attempts, error, upload_ref, duration,
              detected_at, downloaded_at, edited_at, uploaded_at, failed_at, cancelled_at, created_at, updated_at
       FROM videos WHERE task_id = ? ORDER BY updated_at DESC LIMIT 500`
    )
    .bind(taskId)
    .all();
  const allVids = all.results || [];
  const statusCounts = {};
  allVids.forEach((v) => { statusCounts[v.status] = (statusCounts[v.status] || 0) + 1; });
  const live = allVids.filter((v) => LIVE_STATUSES.includes(v.status)).slice(0, 30);
  const recent = allVids.slice(0, 12);

  return {
    task: { id: task.id, name: task.name, source_url: task.source_url, platform: task.platform, dest_platforms: task.dest_platforms, status: task.status },
    range,
    from: win.from,
    to: win.to,
    bucket: win.bucket,
    totals,
    series,
    status_counts: statusCounts,
    in_progress: live.length,
    retries_total: allVids.reduce((s, v) => s + (v.retry_count || 0), 0),
    avg_minutes: nMs ? Math.round((sumMs / nMs) / 60000 * 10) / 10 : null,
    live,
    recent,
    all_time: {
      total: allVids.length,
      uploaded: statusCounts.uploaded || 0,
      failed: statusCounts.failed || 0,
      cancelled: statusCounts.cancelled || 0,
    },
  };
}


async function listLogins(db, { withTokens = false, user = null } = {}) {
  const res = user
    ? await db
        .prepare('SELECT * FROM logins WHERE (? = 1 OR user_id = ?) ORDER BY id DESC')
        .bind(user.is_admin ? 1 : 0, user.id)
        .all()
    : await db.prepare('SELECT * FROM logins ORDER BY id DESC').all();
  const rows = res.results || [];
  return rows.map((r) => {
    const o = { id: r.id, provider: r.provider, label: r.label, channel_title: r.channel_title, user_id: r.user_id, created_at: r.created_at };
    if (withTokens) {
      try {
        o.token = JSON.parse(r.token_json);
      } catch {
        o.token = null;
      }
    }
    return o;
  });
}

async function getLogin(db, id, { withTokens = false } = {}) {
  const r = await db.prepare('SELECT * FROM logins WHERE id = ?').bind(id).first();
  if (!r) return null;
  const o = { id: r.id, provider: r.provider, label: r.label, channel_title: r.channel_title, user_id: r.user_id, created_at: r.created_at };
  if (withTokens) {
    try {
      o.token = JSON.parse(r.token_json);
    } catch {
      o.token = null;
    }
  }
  return o;
}

async function saveLogin(db, body) {
  const tokenJson = JSON.stringify(body.token || {});
  const res = await db
    .prepare(
      `INSERT INTO logins (provider, label, channel_title, token_json, user_id, created_at)
       VALUES (?, ?, ?, ?, ?, ?)`
    )
    .bind(body.provider || 'youtube', body.label || null, body.channel_title || null, tokenJson, body.user_id || 0, now())
    .run();
  return getLogin(db, res.meta?.last_row_id, { withTokens: true });
}

async function deleteLogin(db, id) {
  const res = await db.prepare('DELETE FROM logins WHERE id = ?').bind(id).run();
  return res.meta?.changes > 0;
}

// --- Accounts (social media cookies) -----------------------------------------

const COOKIE_DOMAINS = {
  tiktok: ['tiktok.com'],
  facebook: ['facebook.com', 'fb.com'],
  instagram: ['instagram.com'],
  youtube: ['youtube.com', 'google.com'],
  twitter: ['twitter.com', 'x.com'],
  x: ['twitter.com', 'x.com'],
};

function validateCookieDomains(platform, cookiesStr) {
  const need = COOKIE_DOMAINS[platform];
  if (!need || !cookiesStr) return;
  const s = String(cookiesStr).toLowerCase();
  if (need.some((d) => s.includes(d))) return;          // correct platform present
  const others = Object.values(COOKIE_DOMAINS)
    .flat()
    .filter((d) => !need.includes(d));
  const foreign = others.find((d) => s.includes(d));
  if (foreign) {
    throw new Error(
      `cookies are for ${foreign}, not ${platform} — export them while logged in on ${need[0]}`
    );
  }
  if (s.includes('pages.dev') || s.includes('workers.dev') || s.includes('novaautomation')) {
    throw new Error(
      `these are not ${platform} login cookies (foreign site data) — re-export them from ${need[0]} while logged in`
    );
  }
  // No recognizable domains (minimal name/value export) — accept.
}

async function listAccounts(db, user) {
  const res = user
    ? await db
        .prepare(
          'SELECT id, platform, username, proxy, country, status, user_id, created_at, updated_at FROM accounts WHERE (? = 1 OR user_id = ?) ORDER BY id DESC'
        )
        .bind(user.is_admin ? 1 : 0, user.id)
        .all()
    : await db
        .prepare('SELECT id, platform, username, proxy, country, status, user_id, created_at, updated_at FROM accounts ORDER BY id DESC')
        .all();
  return res.results || [];
}

async function getAccount(db, id) {
  const r = await db.prepare('SELECT id, platform, username, proxy, country, status, user_id, created_at, updated_at FROM accounts WHERE id = ?').bind(id).first();
  return r;
}

async function saveAccount(db, body) {
  const platform = (body.platform || '').toLowerCase().trim();
  if (!platform) throw new Error('platform required');
  const cookies = body.cookies;
  if (!cookies) throw new Error('cookies required');
  const cookiesStr = typeof cookies === 'string' ? cookies : JSON.stringify(cookies);
  validateCookieDomains(platform, cookiesStr);
  const username = body.username || null;
  const proxy = (body.proxy || '').trim();
  const country = (body.country || '').trim();
  const userId = body.user_id || 0;
  const t = now();
  // Upsert: per user + platform + username (multiple accounts per platform, isolated per user)
  const existing = username
    ? await db.prepare('SELECT id FROM accounts WHERE platform = ? AND username = ? AND user_id = ?').bind(platform, username, userId).first()
    : await db.prepare('SELECT id FROM accounts WHERE platform = ? AND (username IS NULL OR username = ?) AND user_id = ?').bind(platform, '', userId).first();
  if (existing) {
    await db.prepare('UPDATE accounts SET username = ?, cookies = ?, proxy = ?, country = ?, updated_at = ? WHERE id = ?')
      .bind(username, cookiesStr, proxy, country, t, existing.id).run();
    return getAccount(db, existing.id);
  }
  const res = await db.prepare('INSERT INTO accounts (platform, username, cookies, proxy, country, status, user_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)')
    .bind(platform, username, cookiesStr, proxy, country, 'active', userId, t, t).run();
  return getAccount(db, res.meta?.last_row_id);
}

async function deleteAccount(db, id) {
  const res = await db.prepare('DELETE FROM accounts WHERE id = ?').bind(id).run();
  return res.meta?.changes > 0;
}

async function getAccountCookies(db, platform) {
  const r = await db.prepare('SELECT cookies FROM accounts WHERE platform = ? AND status = ?')
    .bind((platform || '').toLowerCase(), 'active').first();
  if (!r) return null;
  try { return JSON.parse(r.cookies); } catch { return r.cookies; }
}

// --- OAuth ------------------------------------------------------------------

function googleAuthUrl(env, state, clientId) {
  const cid = clientId || env.YT_CLIENT_ID;
  const redirect = `${env.PUBLIC_BASE_URL}/api/youtube/callback`;
  const params = new URLSearchParams({
    client_id: cid,
    redirect_uri: redirect,
    response_type: 'code',
    scope: 'https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly',
    access_type: 'offline',
    prompt: 'consent',
    state,
  });
  return `https://accounts.google.com/o/oauth2/v2/auth?${params.toString()}`;
}

async function exchangeCode(env, code, clientId, clientSecret) {
  const cid = clientId || env.YT_CLIENT_ID;
  const sec = clientSecret || env.YT_CLIENT_SECRET;
  const redirect = `${env.PUBLIC_BASE_URL}/api/youtube/callback`;
  const body = new URLSearchParams({
    code,
    client_id: cid,
    client_secret: sec,
    redirect_uri: redirect,
    grant_type: 'authorization_code',
  });
  const res = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error_description || data.error || 'token exchange failed');
  return data;
}

async function fetchChannelTitle(accessToken) {
  try {
    const res = await fetch(
      'https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true',
      { headers: { authorization: `Bearer ${accessToken}` } }
    );
    const data = await res.json();
    return data.items?.[0]?.snippet?.title || null;
  } catch {
    return null;
  }
}

// --- Internal auth ----------------------------------------------------------

function checkInternal(req, env) {
  const token = req.headers.get('x-token') || '';
  return token && token === env.INTERNAL_TOKEN;
}

// --- User auth (PBKDF2 passwords + DB sessions) -----------------------------

function hexToBytes(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

function bytesToHex(bytes) {
  return [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function derivePassword(password, saltHex, iterations) {
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', salt: hexToBytes(saltHex), iterations, hash: 'SHA-256' },
    key,
    256
  );
  return bytesToHex(new Uint8Array(bits));
}

async function hashPassword(password) {
  // Cloudflare Workers Web Crypto caps PBKDF2 iterations at 100000.
  const iterations = 100000;
  const salt = bytesToHex(crypto.getRandomValues(new Uint8Array(16)));
  const hash = await derivePassword(password, salt, iterations);
  return `pbkdf2$${iterations}$${salt}$${hash}`;
}

async function verifyPassword(password, stored) {
  try {
    const [algo, iterStr, salt, hash] = String(stored).split('$');
    if (algo !== 'pbkdf2') return false;
    const calc = await derivePassword(password, salt, parseInt(iterStr, 10));
    if (calc.length !== hash.length) return false;
    let diff = 0;
    for (let i = 0; i < calc.length; i++) diff |= calc.charCodeAt(i) ^ hash.charCodeAt(i);
    return diff === 0;
  } catch {
    return false;
  }
}

async function findUserByIdentity(db, identity) {
  const id = String(identity || '').trim().toLowerCase();
  if (!id) return null;
  return db
    .prepare('SELECT * FROM users WHERE lower(email) = ? OR lower(username) = ?')
    .bind(id, id)
    .first();
}

async function createSession(db, userId) {
  const token = bytesToHex(crypto.getRandomValues(new Uint8Array(32)));
  const t = now();
  await db
    .prepare('INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)')
    .bind(token, userId, t, t + 30 * 24 * 60 * 60 * 1000)
    .run();
  db.prepare('DELETE FROM sessions WHERE expires_at < ?').bind(t).run().catch(() => {});
  return token;
}

async function userFromRequest(req, env) {
  const h = req.headers.get('authorization') || '';
  if (!h.toLowerCase().startsWith('bearer ')) return null;
  const token = h.slice(7).trim();
  if (!token) return null;
  const row = await env.DB
    .prepare(
      'SELECT u.id, u.username, u.email, u.is_admin, u.created_at, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?'
    )
    .bind(token)
    .first();
  if (!row || row.expires_at < now()) return null;
  return { id: row.id, username: row.username, email: row.email, is_admin: !!row.is_admin, created_at: row.created_at };
}

// Fire GitHub process.yml dispatch. Primary trigger for both new videos and
// manual jobs (fastpoll only detects; it fires only as a fallback).
async function dispatchProcess(env, inputs) {
  const token = env.GH_TOKEN;
  const repo = env.GH_REPO || 'asifali-git/novaautomation';
  if (!token) throw new Error('GH_TOKEN not set on worker');
  const r = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/process.yml/dispatches`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Accept': 'application/vnd.github+json',
      'Content-Type': 'application/json',
      'User-Agent': 'novaautomation-worker', // GitHub API rejects requests without it
    },
    body: JSON.stringify({ ref: 'master', inputs }),
  });
  if (!r.ok) {
    const t = await r.text().catch(() => '');
    throw new Error(`gh ${r.status}: ${t.slice(0, 200)}`);
  }
  return true;
}

// --- Router -----------------------------------------------------------------

export default {
  // Cron every 5 min: (1) ping fastpoll's /wake so Render free tier never
  // sleeps (yt-dlp detection needs it alive; GH can't poll every 15-30s),
  // (2) sweep stranded videos/jobs (created but GH dispatch missed).
  async scheduled(_event, env, ctx) {
    ctx.waitUntil((async () => {
      const wakeUrl = env.RENDER_WAKE_URL || 'https://novapoll.onrender.com/wake';
      try {
        const r = await fetch(wakeUrl, { method: 'GET' });
        console.log('render wake', r.status);
      } catch (e) {
        console.log('render wake fail', e && e.message);
      }
      // Stranded videos: detected, never dispatched, >5 min old.
      try {
        const cutoff = now() - 5 * 60 * 1000;
        const rows = await env.DB.prepare(
          "SELECT id FROM videos WHERE status = 'detected' AND (dispatched_at IS NULL OR dispatched_at = 0) AND created_at < ? ORDER BY id ASC LIMIT 10"
        ).bind(cutoff).all();
        const ids = (rows.results || []).map((r) => r.id);
        if (ids.length) {
          const ok = await dispatchProcess(env, { video_ids: ids.join(',') });
          if (ok) {
            for (const id of ids) {
              await env.DB.prepare('UPDATE videos SET dispatched_at = ? WHERE id = ?').bind(now(), id).run();
            }
          }
          console.log('sweep dispatched videos', ids.join(','), ok ? 'ok' : 'failed');
        }
      } catch (e) {
        console.log('video sweep fail', e && e.message);
      }
      // Auto-retry: failed BEFORE the upload stage (edited_at still null), up to
      // 3 total runs. Upload-stage failures are excluded — they already get 3
      // in-run upload attempts, and auto-re-running those would burn YT quota.
      try {
        const rows = await env.DB.prepare(
          `SELECT id FROM videos
           WHERE status = 'failed' AND retry_count < 3 AND edited_at IS NULL
             AND COALESCE(failed_at, updated_at) < ?
           ORDER BY id ASC LIMIT 5`
        ).bind(now() - 2 * 60 * 1000).all();
        const ids = (rows.results || []).map((r) => r.id);
        if (ids.length) {
          for (const id of ids) {
            await env.DB.prepare(
              `UPDATE videos SET status = 'detected', retry_count = retry_count + 1,
                                 error = NULL, failed_at = NULL, dispatched_at = NULL, updated_at = ?
               WHERE id = ? AND status = 'failed'`
            ).bind(now(), id).run();
          }
          try {
            const ok = await dispatchProcess(env, { video_ids: ids.join(',') });
            if (ok) {
              for (const id of ids) {
                await env.DB.prepare('UPDATE videos SET dispatched_at = ? WHERE id = ? AND dispatched_at IS NULL').bind(now(), id).run();
              }
            }
            console.log('auto-retry dispatched videos', ids.join(','), ok ? 'ok' : 'failed');
          } catch (e) {
            console.log('auto-retry dispatch fail', e && e.message);
          }
        }
      } catch (e) {
        console.log('auto-retry sweep fail', e && e.message);
      }
      // Stranded manual jobs: queued, never dispatched, >5 min old.
      try {
        const cutoff = now() - 5 * 60 * 1000;
        const rows = await env.DB.prepare(
          "SELECT id FROM manual_jobs WHERE status = 'queued' AND (dispatched_at IS NULL OR dispatched_at = 0) AND created_at < ? ORDER BY id ASC LIMIT 5"
        ).bind(cutoff).all();
        const ids = (rows.results || []).map((r) => r.id);
        if (ids.length) {
          for (const id of ids) {
            try {
              await dispatchProcess(env, { manual_job_id: String(id) });
              await env.DB.prepare('UPDATE manual_jobs SET dispatched_at = ? WHERE id = ?').bind(now(), id).run();
              console.log('sweep dispatched manual job', id);
            } catch (e) {
              console.log('sweep manual job', id, 'fail', e && e.message);
            }
          }
        }
      } catch (e) {
        console.log('manual sweep fail', e && e.message);
      }
    })());
  },
  async fetch(req, env, cctx) {
    const url = new URL(req.url);
    const path = url.pathname;
    const origin = req.headers.get('origin') || '';

    if (req.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders(origin, env) });
    }

    const cors = corsHeaders(origin, env);
    const withCors = (r) => {
      const h = new Headers(r.headers);
      Object.entries(cors).forEach(([k, v]) => h.set(k, v));
      return new Response(r.body, { status: r.status, statusText: r.statusText, headers: h });
    };

    try {
      // ---- Auth gate: every app API needs a logged-in user ----
      const isPublic =
        path === '/api/health' ||
        path.startsWith('/api/auth/') ||
        path === '/api/youtube/callback' ||
        path.startsWith('/api/internal/');
      let gateUser = null;
      if (!isPublic) {
        gateUser = await userFromRequest(req, env);
        if (!gateUser) return withCors(json({ error: 'auth required' }, 401));
      }

      // ---- Auth (register / login / session) ----
      if (path === '/api/auth/register' && req.method === 'POST') {
        const body = await readBody(req);
        const username = String(body.username || '').trim();
        const email = String(body.email || '').trim();
        const password = String(body.password || '');
        if (!/^[a-zA-Z0-9_.-]{3,30}$/.test(username)) {
          return withCors(json({ error: 'Username: 3-30 chars (letters, digits, _ . -)' }, 400));
        }
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
          return withCors(json({ error: 'Enter a valid email' }, 400));
        }
        if (password.length < 6) {
          return withCors(json({ error: 'Password must be at least 6 characters' }, 400));
        }
        const dupe = await env.DB
          .prepare('SELECT id FROM users WHERE lower(username) = ? OR lower(email) = ?')
          .bind(username.toLowerCase(), email.toLowerCase())
          .first();
        if (dupe) return withCors(json({ error: 'Username or email already registered' }, 409));
        const pwHash = await hashPassword(password);
        // First registered user becomes admin (sees all data).
        const cnt = await env.DB.prepare('SELECT COUNT(*) AS c FROM users').first();
        const isAdmin = (cnt?.c || 0) === 0 ? 1 : 0;
        const res = await env.DB
          .prepare('INSERT INTO users (username, email, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?, ?)')
          .bind(username, email, pwHash, isAdmin, now())
          .run();
        const user = { id: res.meta?.last_row_id, username, email, is_admin: !!isAdmin };
        const token = await createSession(env.DB, user.id);
        return withCors(json({ token, user }, 201));
      }

      if (path === '/api/auth/login' && req.method === 'POST') {
        const body = await readBody(req);
        const identity = String(body.identity || '').trim();
        const password = String(body.password || '');
        const user = await findUserByIdentity(env.DB, identity);
        const ok = user && (await verifyPassword(password, user.password_hash));
        if (!ok) return withCors(json({ error: 'Invalid credentials' }, 401));
        const token = await createSession(env.DB, user.id);
        return withCors(
          json({
            token,
            user: { id: user.id, username: user.username, email: user.email, is_admin: !!user.is_admin },
          })
        );
      }

      if (path === '/api/auth/logout' && req.method === 'POST') {
        const h = req.headers.get('authorization') || '';
        const token = h.toLowerCase().startsWith('bearer ') ? h.slice(7).trim() : '';
        if (token) await env.DB.prepare('DELETE FROM sessions WHERE token = ?').bind(token).run();
        return withCors(json({ ok: true }));
      }

      if (path === '/api/auth/me' && req.method === 'GET') {
        const user = await userFromRequest(req, env);
        if (!user) return withCors(json({ error: 'auth required' }, 401));
        return withCors(json({ user }));
      }

      // ---- Public API (frontend) ----
      if (path === '/api/state' && req.method === 'GET') {
        const [tasks, videos, logins, accounts] = await Promise.all([
          listTasks(env.DB, gateUser),
          listVideos(env.DB, { limit: 200, user: gateUser }),
          listLogins(env.DB, { user: gateUser }),
          listAccounts(env.DB, gateUser),
        ]);
        return withCors(
          json({
            tasks,
            videos,
            logins,
            accounts,
            me: gateUser ? { id: gateUser.id, username: gateUser.username, is_admin: gateUser.is_admin } : null,
            config: { has_client_id: !!env.YT_CLIENT_ID, has_client_secret: !!env.YT_CLIENT_SECRET },
          })
        );
      }

      // ---- Accounts (social cookies) ----
      if (path === '/api/accounts' && req.method === 'GET') {
        const accounts = await listAccounts(env.DB, gateUser);
        return withCors(json({ accounts }));
      }

      if (path === '/api/accounts' && req.method === 'POST') {
        const body = await readBody(req);
        body.user_id = gateUser.id;
        let account;
        try {
          account = await saveAccount(env.DB, body);
        } catch (e) {
          return withCors(json({ error: String(e.message || e) }, 400));
        }
        return withCors(json({ account }, 201));
      }

      if (path.startsWith('/api/accounts/') && req.method === 'DELETE') {
        const id = Number(path.split('/')[3]);
        const row = await getAccount(env.DB, id);
        if (!row) return withCors(json({ ok: true }));
        if (!gateUser.is_admin && row.user_id !== gateUser.id) {
          return withCors(json({ error: 'forbidden' }, 403));
        }
        const ok = await deleteAccount(env.DB, id);
        return withCors(json({ ok }));
      }

      // ---- Update cookies (and optionally username/proxy/country) of an existing account ----
      if (path.startsWith('/api/accounts/') && req.method === 'PATCH') {
        const id = Number(path.split('/')[3]);
        const row = await getAccount(env.DB, id);
        if (!row) return withCors(json({ error: 'not found' }, 404));
        if (!gateUser.is_admin && row.user_id !== gateUser.id) {
          return withCors(json({ error: 'forbidden' }, 403));
        }
        const body = await readBody(req);
        const cookies = body.cookies;
        if (!cookies) return withCors(json({ error: 'cookies required' }, 400));
        const cookiesStr = typeof cookies === 'string' ? cookies : JSON.stringify(cookies);
        try {
          validateCookieDomains(row.platform, cookiesStr);
        } catch (e) {
          return withCors(json({ error: String(e.message || e) }, 400));
        }
        const username = body.username !== undefined ? (body.username || null) : row.username;
        const proxy = body.proxy !== undefined ? String(body.proxy || '').trim() : row.proxy;
        const country = body.country !== undefined ? String(body.country || '').trim() : row.country;
        await env.DB.prepare(
          'UPDATE accounts SET username = ?, cookies = ?, proxy = ?, country = ?, updated_at = ? WHERE id = ?'
        ).bind(username, cookiesStr, proxy, country, now(), id).run();
        return withCors(json({ account: await getAccount(env.DB, id) }));
      }

      // ---- YouTube logins (no tokens exposed) ----
      if (path === '/api/logins' && req.method === 'GET') {
        const logins = await listLogins(env.DB, { user: gateUser });
        return withCors(json({ logins }));
      }

      // ---- Videos (scoped) ----
      if (path === '/api/videos' && req.method === 'GET') {
        const taskId = url.searchParams.get('task_id');
        const status = url.searchParams.get('status'); // comma list, or 'live'
        const limit = Math.min(Number(url.searchParams.get('limit')) || 200, 500);
        const filters = [];
        const vals = [];
        if (taskId) { filters.push('v.task_id = ?'); vals.push(Number(taskId)); }
        if (status) {
          const wanted = status === 'live'
            ? LIVE_STATUSES
            : status.split(',').map((s) => s.trim()).filter(Boolean);
          if (wanted.length) {
            filters.push(`v.status IN (${wanted.map(() => '?').join(',')})`);
            vals.push(...wanted);
          }
        }
        const scope = ' AND (? = 1 OR v.user_id = ?)';
        const scopeVals = [gateUser.is_admin ? 1 : 0, gateUser.id];
        const res = await env.DB.prepare(
          `SELECT v.*, t.name AS source_name, t.source_url AS source
           FROM videos v LEFT JOIN tasks t ON v.task_id = t.id
           WHERE 1=1${filters.length ? ` AND ${filters.join(' AND ')}` : ''}${scope}
           ORDER BY v.updated_at DESC LIMIT ?`
        ).bind(...vals, ...scopeVals, limit).all();
        return withCors(json({ videos: res.results || [] }));
      }

      // ---- Per-job analytics (monitor card drill-down) ----
      const taskAnalyticsMatch = path.match(/^\/api\/tasks\/(\d+)\/analytics$/);
      if (taskAnalyticsMatch && req.method === 'GET') {
        const taskId = Number(taskAnalyticsMatch[1]);
        const data = await taskAnalytics(env.DB, {
          taskId,
          user: gateUser,
          range: url.searchParams.get('range') || 'week',
          from: url.searchParams.get('from'),
          to: url.searchParams.get('to'),
        });
        if (!data) return withCors(json({ error: 'not found' }, 404));
        if (data.forbidden) return withCors(json({ error: 'forbidden' }, 403));
        return withCors(json(data));
      }

      // ---- Cancel a live video (stops further pipeline work) ----
      const vCancel = path.match(/^\/api\/videos\/(\d+)\/cancel$/);
      if (vCancel && req.method === 'POST') {
        const id = Number(vCancel[1]);
        const video = await getVideo(env.DB, id);
        if (!video) return withCors(json({ error: 'not found' }, 404));
        if (!gateUser.is_admin && video.user_id !== gateUser.id) {
          return withCors(json({ error: 'forbidden' }, 403));
        }
        if (TERMINAL_STATUSES.includes(video.status)) {
          return withCors(json({ error: `already ${video.status} — can't cancel` }, 400));
        }
        const res = await env.DB.prepare(
          `UPDATE videos SET status = 'cancelled', cancelled_at = ?, error = NULL, updated_at = ?
           WHERE id = ? AND status IN (${LIVE_STATUSES.map(() => '?').join(',')})`
        ).bind(now(), now(), id, ...LIVE_STATUSES).run();
        if (!res.meta?.changes) return withCors(json({ error: 'video already finished' }, 400));
        return withCors(json({ video: await getVideo(env.DB, id) }));
      }

      // ---- Retry a failed/cancelled video (re-dispatch GH pipeline) ----
      const vRetry = path.match(/^\/api\/videos\/(\d+)\/retry$/);
      if (vRetry && req.method === 'POST') {
        const id = Number(vRetry[1]);
        const video = await getVideo(env.DB, id);
        if (!video) return withCors(json({ error: 'not found' }, 404));
        if (!gateUser.is_admin && video.user_id !== gateUser.id) {
          return withCors(json({ error: 'forbidden' }, 403));
        }
        if (video.status === 'uploaded') return withCors(json({ error: 'already uploaded' }, 400));
        if (video.status === 'detected') return withCors(json({ error: 'already queued' }, 400));
        const force = !!url.searchParams.get('force');
        if (!force && (video.retry_count || 0) >= 3) {
          return withCors(json({ error: 'retry limit reached (3) — use force to override' }, 400));
        }
        await env.DB.prepare(
          `UPDATE videos SET status = 'detected', error = NULL, failed_at = NULL, cancelled_at = NULL,
                             dispatched_at = NULL, retry_count = retry_count + 1, updated_at = ?
           WHERE id = ?`
        ).bind(now(), id).run();
        let dispatched = false;
        let dispatchError = null;
        try {
          dispatched = await dispatchProcess(env, { video_ids: String(id) });
          if (dispatched) {
            await env.DB.prepare('UPDATE videos SET dispatched_at = ? WHERE id = ?').bind(now(), id).run();
          }
        } catch (e) {
          dispatchError = e.message || String(e);
        }
        return withCors(json({ video: await getVideo(env.DB, id), dispatched, dispatch_error: dispatchError }));
      }

      if (path === '/api/health' && req.method === 'GET') {
        return withCors(json({ ok: true, t: now() }));
      }

      // ---- Manual upload jobs ----
      if (path === '/api/manual/upload' && req.method === 'POST') {
        const body = await readBody(req);
        const url = (body.url || '').trim();
        if (!url) return withCors(json({ error: 'url required' }, 400));
        const dest = Array.isArray(body.dest_platforms) ? body.dest_platforms.join(',') : (body.dest_platforms || 'youtube');
        const res = await env.DB
          .prepare('INSERT INTO manual_jobs (url, title, dest_platforms, status, user_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)')
          .bind(url, body.title || null, dest, 'queued', gateUser.id, now(), now())
          .run();
        const job = await env.DB.prepare('SELECT * FROM manual_jobs WHERE id = ?').bind(res.meta?.last_row_id).first();
        // Instant GH trigger (synchronous — result visible in response).
        let dispatched = false;
        let dispatchError = null;
        if (job) {
          try {
            dispatched = await dispatchProcess(env, { manual_job_id: String(job.id) });
            if (!dispatched) dispatchError = 'dispatch request failed';
          } catch (e) {
            dispatchError = (e && e.message) || String(e);
          }
          if (dispatched) {
            // Record so fastpoll (backup) doesn't create a duplicate GH run.
            await env.DB.prepare('UPDATE manual_jobs SET dispatched_at = ? WHERE id = ?')
              .bind(now(), job.id).run().catch(() => {});
            job.dispatched_at = now();
          }
          console.log('gh dispatch for job', job.id, dispatched ? 'ok' : 'failed', dispatchError || '');
        }
        return withCors(json({ job, dispatched, dispatch_error: dispatchError }, 201));
      }

      if (path === '/api/manual/jobs' && req.method === 'GET') {
        const res = await env.DB
          .prepare('SELECT * FROM manual_jobs WHERE (? = 1 OR user_id = ?) ORDER BY id DESC LIMIT 50')
          .bind(gateUser.is_admin ? 1 : 0, gateUser.id)
          .all();
        return withCors(json({ jobs: res.results || [] }));
      }

      if (path.startsWith('/api/manual/jobs/') && req.method === 'DELETE') {
        const id = Number(path.split('/')[4]);
        const row = await env.DB.prepare('SELECT user_id FROM manual_jobs WHERE id = ?').bind(id).first();
        if (row && !gateUser.is_admin && row.user_id !== gateUser.id) {
          return withCors(json({ error: 'forbidden' }, 403));
        }
        await env.DB.prepare('DELETE FROM manual_jobs WHERE id = ?').bind(id).run();
        return withCors(json({ ok: true }));
      }

      // ---- URL detection: platform + channel/page name ----
      if (path === '/api/detect' && req.method === 'GET') {
        const target = url.searchParams.get('url') || '';
        if (!target) return withCors(json({ error: 'url required' }, 400));
        const platform = detectPlatform(target);
        let name = '';

        // Extract username/handle from URL path directly (works even when fetch fails)
        try {
          const u = new URL(target);
          const parts = u.pathname.split('/').filter(Boolean);
          if (platform === 'youtube') {
            // /@handle, /channel/UC..., /c/name, /user/name
            if (parts[0]?.startsWith('@')) name = parts[0].slice(1);
            else if (parts[0] === 'channel' && parts[1]) name = parts[1];
            else if ((parts[0] === 'c' || parts[0] === 'user') && parts[1]) name = parts[1];
          } else if (platform === 'tiktok' && parts[0]) {
            name = parts[0].replace(/^@/, '');
          } else if (platform === 'instagram' && parts[0]) {
            name = parts[0].replace(/^@/, '');
          } else if (platform === 'facebook' && parts[0]) {
            // facebook.com/pagename or facebook.com/profile.php?id=123
            if (parts[0] === 'profile.php') {
              name = u.searchParams.get('id') || '';
            } else if (parts[0] !== 'watch' && parts[0] !== 'reel' && parts[0] !== 'video') {
              name = parts[0];
            }
          }
          name = name.replace(/_/g, ' ').trim();
        } catch (_) {}

        // Also try fetching page title for a nicer display name
        try {
          const isFbMedia = platform === 'facebook' && ['reel', 'watch', 'video', 'share'].some(k => url.pathname.includes(`/${k}/`));
          const resp = await fetch(target, {
            headers: {
              'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
              'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
              'accept-language': 'en-US,en;q=0.9',
              'sec-fetch-dest': 'document',
              'sec-fetch-mode': 'navigate',
              'sec-fetch-site': 'none',
              'upgrade-insecure-requests': '1',
            },
            redirect: 'follow',
            signal: AbortSignal.timeout(8000),
          });
          const html = await resp.text();

          // Facebook media pages embed page name in JSON — try those first
          if (!name && isFbMedia) {
            const patterns = [
              /"pageName":"([^"]{2,80})"/,
              /&quot;pageName&quot;:&quot;([^&]{2,80})&quot;/,
              /"username":"([^"]{2,80})"/,
              /"author":"([^"]{2,80})"/,
            ];
            for (const p of patterns) {
              const m = html.match(p);
              if (m && m[1] && !/^(login|guest|user|undefined)/i.test(m[1])) {
                name = m[1].replace(/_/g, ' ').trim();
                break;
              }
            }
          }

          const ogTitle = html.match(/<meta[^>]+property=["']og:title["'][^>]+content=["']([^"']+)["']/i)
            || html.match(/<meta[^>]+content=["']([^"']+)["'][^>]+property=["']og:title["']/i);
          const titleTag = html.match(/<title[^>]*>([^<]+)<\/title>/i);
          let fetched = (ogTitle?.[1] || titleTag?.[1] || '').trim();

          // Facebook og:title looks like "PageName posted a video..." / "PageName - Home | Facebook"
          if (!name && platform === 'facebook' && fetched) {
            const m = fetched.match(/^(.{2,80}?)\s+(posted|is on|home|home\s*\|)/i);
            if (m) name = m[1].trim();
          }

          // Clean generic/login titles
          fetched = fetched
            .replace(/\s*[-–|]\s*(YouTube|Facebook|Instagram|TikTok)\s*$/i, '')
            .replace(/^(Log in|Sign up|Login|Welcome).*$/i, '')
            .replace(/^(Facebook|Instagram|TikTok)\s*[-–|].*$/i, '')
            .trim();

          // Use fetched name if it looks like a real name, else keep URL-derived
          // Only use fetched title if we don't already have a name (URL/pageName)
          if (!name && fetched.length >= 2 && fetched.length <= 80 && !/^(log in|sign up|error|undefined|null)/i.test(fetched)) {
            name = fetched;
          }
          if (name.length > 80) name = name.slice(0, 80);
        } catch (e) {
          // Fetch failed — URL-derived name is enough
        }

        return withCors(json({ platform, name }));
      }

      if (path === '/api/tasks' && req.method === 'GET') {
        const tasks = await listTasks(env.DB, gateUser);
        return withCors(json({ tasks }));
      }

      if (path === '/api/tasks' && req.method === 'POST') {
        const body = await readBody(req);
        body.user_id = gateUser.id;
        const task = await createTask(env.DB, body);
        return withCors(json({ task }, 201));
      }

      if (path.startsWith('/api/tasks/') && req.method === 'PATCH') {
        const id = Number(path.split('/')[3]);
        const row = await getTask(env.DB, id);
        if (row && !gateUser.is_admin && row.user_id !== gateUser.id) {
          return withCors(json({ error: 'forbidden' }, 403));
        }
        const body = await readBody(req);
        const task = await patchTask(env.DB, id, body);
        if (!task) return withCors(json({ error: 'not found' }, 404));
        return withCors(json({ task }));
      }

      if (path.startsWith('/api/tasks/') && req.method === 'DELETE') {
        const id = Number(path.split('/')[3]);
        const row = await getTask(env.DB, id);
        if (row && !gateUser.is_admin && row.user_id !== gateUser.id) {
          return withCors(json({ error: 'forbidden' }, 403));
        }
        const ok = await deleteTask(env.DB, id);
        return withCors(json({ ok }));
      }

      if (path.startsWith('/api/logins/') && path.endsWith('/refresh-token') && req.method === 'GET') {
        const id = Number(path.split('/')[3]);
        const login = await getLogin(env.DB, id, { withTokens: true });
        if (!login) return withCors(json({ error: 'not found' }, 404));
        if (!gateUser.is_admin && login.user_id !== gateUser.id) {
          return withCors(json({ error: 'forbidden' }, 403));
        }
        return withCors(json({ refresh_token: login.token?.refresh_token || null }));
      }

      if (path.startsWith('/api/logins/') && req.method === 'DELETE') {
        const id = Number(path.split('/')[3]);
        const login = await getLogin(env.DB, id);
        if (login && !gateUser.is_admin && login.user_id !== gateUser.id) {
          return withCors(json({ error: 'forbidden' }, 403));
        }
        const ok = await deleteLogin(env.DB, id);
        return withCors(json({ ok }));
      }

      if (path === '/api/youtube/login/start' && req.method === 'POST') {
        const body = await readBody(req);
        const state = crypto.randomUUID();
        // Remember who initiated so the public callback can attribute the login.
        await env.DB
          .prepare('INSERT OR REPLACE INTO oauth_states (state, user_id, created_at) VALUES (?, ?, ?)')
          .bind(state, gateUser.id, now())
          .run();
        env.DB.prepare('DELETE FROM oauth_states WHERE created_at < ?').bind(now() - 60 * 60 * 1000).run().catch(() => {});
        const auth_url = googleAuthUrl(env, state, body.client_id);
        return withCors(json({ auth_url, state }));
      }

      if (path === '/api/youtube/callback' && req.method === 'GET') {
        const code = url.searchParams.get('code');
        const state = url.searchParams.get('state') || '';
        const err = url.searchParams.get('error');
        const clientId = url.searchParams.get('client_id') || null;
        const clientSecret = url.searchParams.get('client_secret') || null;

        // Redirect back to the app (same tab flow) instead of dead-end HTML.
        const appBase = (env.ALLOWED_ORIGIN && env.ALLOWED_ORIGIN !== '*')
          ? env.ALLOWED_ORIGIN
          : 'https://novaautomation.pages.dev';
        const redirectTo = (params, hash = 'youtube') =>
          Response.redirect(`${appBase}/?${params}#${hash}`, 302);

        if (err || !code) {
          return redirectTo(`yt=error&msg=${encodeURIComponent(err || 'no code')}`);
        }
        // Attribute the new login to the user who started the flow.
        const srow = await env.DB.prepare('SELECT user_id FROM oauth_states WHERE state = ?').bind(state).first();
        if (!srow) {
          return redirectTo('yt=error&msg=expired%20or%20unknown%20state');
        }
        env.DB.prepare('DELETE FROM oauth_states WHERE state = ?').bind(state).run().catch(() => {});
        try {
          const token = await exchangeCode(env, code, clientId, clientSecret);
          // Persist client creds with the login so runners don't depend on worker env fallback.
          token.client_id = clientId || env.YT_CLIENT_ID;
          token.client_secret = clientSecret || env.YT_CLIENT_SECRET;
          const channel = token.access_token ? await fetchChannelTitle(token.access_token) : null;
          const login = await saveLogin(env.DB, {
            provider: 'youtube',
            label: channel || 'YouTube channel',
            channel_title: channel,
            token,
            user_id: srow.user_id,
          });
          const ch = channel ? encodeURIComponent(channel) : '';
          return redirectTo(`yt=connected&channel=${ch}&id=${login.id}`);
        } catch (e) {
          return redirectTo(`yt=error&msg=${encodeURIComponent(e.message)}`);
        }
      }

      // ---- Internal API (CI runner) ----
      if (path.startsWith('/api/internal/')) {
        if (!checkInternal(req, env)) return withCors(json({ error: 'unauthorized' }, 401));

        if (path === '/api/internal/tasks' && req.method === 'GET') {
          const tasks = (await listTasks(env.DB)).filter((t) => t.status === 'active');
          return withCors(json({ tasks }));
        }

        // Internal task PATCH — pollers (poll.py / fastpoll.py) run with x-token only.
        const itaskPatch = path.match(/^\/api\/internal\/tasks\/(\d+)$/);
        if (itaskPatch && req.method === 'PATCH') {
          const id = Number(itaskPatch[1]);
          const body = await readBody(req);
          const task = await patchTask(env.DB, id, body);
          if (!task) return withCors(json({ error: 'not found' }, 404));
          return withCors(json({ task }));
        }

        // Manual jobs — peek at next queued job (no status change; runner claims via POST claim)
        if (path === '/api/internal/manual/next' && req.method === 'GET') {
          const job = await env.DB.prepare('SELECT * FROM manual_jobs WHERE status = ? ORDER BY id ASC LIMIT 1').bind('queued').first();
          return withCors(json({ job: job || null }));
        }

        // Atomic claim: queued -> processing (only one runner wins).
        // Also recovers stale processing jobs (stuck > 30 min, e.g. old fastpoll or dead runner).
        const manualClaim = path.match(/^\/api\/internal\/manual\/(\d+)\/claim$/);
        if (manualClaim && req.method === 'POST') {
          const id = Number(manualClaim[1]);
          const staleCutoff = now() - 30 * 60 * 1000;
          const res = await env.DB
            .prepare(
              "UPDATE manual_jobs SET status = 'processing', updated_at = ? WHERE id = ? AND (status = 'queued' OR (status = 'processing' AND updated_at < ?))"
            )
            .bind(now(), id, staleCutoff)
            .run();
          const claimed = (res.meta?.changes || 0) > 0;
          const job = await env.DB.prepare('SELECT * FROM manual_jobs WHERE id = ?').bind(id).first();
          return withCors(json({ claimed, job: job || null }));
        }

        // Fetch a specific manual job by id (no status change)
        const manualGet = path.match(/^\/api\/internal\/manual\/(\d+)$/);
        if (manualGet && req.method === 'GET') {
          const id = Number(manualGet[1]);
          const job = await env.DB.prepare('SELECT * FROM manual_jobs WHERE id = ?').bind(id).first();
          if (!job) return withCors(json({ error: 'not found' }, 404));
          return withCors(json({ job }));
        }

        // Update manual job status
        const manualPatch = path.match(/^\/api\/internal\/manual\/(\d+)$/);
        if (manualPatch && req.method === 'PATCH') {
          const id = Number(manualPatch[1]);
          const body = await readBody(req);
          const fields = [];
          const vals = [];
          for (const k of ['status', 'result', 'error', 'title']) {
            if (k in body) {
              fields.push(`${k} = ?`);
              vals.push(k === 'result' || k === 'error' ? JSON.stringify(body[k]) : body[k]);
            }
          }
          if (fields.length) {
            vals.push(now(), id);
            await env.DB.prepare(`UPDATE manual_jobs SET ${fields.join(', ')}, updated_at = ? WHERE id = ?`).bind(...vals).run();
          }
          const job = await env.DB.prepare('SELECT * FROM manual_jobs WHERE id = ?').bind(id).first();
          return withCors(json({ job }));
        }

        if (path === '/api/internal/videos/next' && req.method === 'GET') {
          const video = await nextPendingVideo(env.DB);
          return withCors(json({ video: video || null }));
        }

        if (/^\/api\/internal\/videos\/\d+$/.test(path) && req.method === 'GET') {
          const id = Number(path.split('/')[4]);
          const video = await getVideo(env.DB, id);
          if (!video) return withCors(json({ error: 'not found' }, 404));
          return withCors(json({ video }));
        }

        if (path === '/api/internal/videos' && req.method === 'POST') {
          const body = await readBody(req);
          const result = await addVideo(env.DB, body);
          // New video -> trigger GH process immediately (fastpoll only detects).
          let dispatched = false;
          if (result.created && result.video) {
            try {
              dispatched = await dispatchProcess(env, { video_ids: String(result.video.id) });
              if (dispatched) {
                await env.DB.prepare('UPDATE videos SET dispatched_at = ? WHERE id = ?')
                  .bind(now(), result.video.id).run().catch(() => {});
                result.video.dispatched_at = now();
              }
            } catch (e) {
              console.log('gh dispatch failed for video', result.video.id, e && e.message);
            }
            console.log('gh dispatch video', result.video.id, dispatched ? 'ok' : 'failed');
          }
          result.dispatched = dispatched;
          return withCors(json(result, result.created ? 201 : 200));
        }

        if (path.startsWith('/api/internal/videos/') && req.method === 'PATCH') {
          const id = Number(path.split('/')[4]);
          const body = await readBody(req);
          const video = await patchVideo(env.DB, id, body);
          return withCors(json({ video }));
        }

        if (path === '/api/internal/logins/env' && req.method === 'GET') {
          // Return usable YouTube login as YT_* env for the runner.
          // ?user_id=N → that user's login; otherwise prefer an admin-owned login.
          const uid = url.searchParams.get('user_id');
          let logins = await listLogins(env.DB, { withTokens: true });
          if (uid) {
            logins = logins.filter((l) => l.user_id === Number(uid));
          } else {
            try {
              const admins = await env.DB.prepare('SELECT id FROM users WHERE is_admin = 1').all();
              const adminIds = new Set((admins.results || []).map((r) => r.id));
              const adminLogin = logins.find((l) => adminIds.has(l.user_id));
              if (adminLogin) logins = [adminLogin, ...logins.filter((l) => l !== adminLogin)];
            } catch { /* keep order */ }
          }
          const yt = logins.find((l) => l.provider === 'youtube' && l.token?.refresh_token);
          return withCors(
            json({
              YT_REFRESH_TOKEN: yt?.token?.refresh_token || null,
              YT_CLIENT_ID: yt?.token?.client_id || env.YT_CLIENT_ID || null,
              YT_CLIENT_SECRET: yt?.token?.client_secret || env.YT_CLIENT_SECRET || null,
              login_id: yt?.id || null,
            })
          );
        }

        if (/^\/api\/internal\/logins\/\d+$/.test(path) && req.method === 'GET') {
          const id = Number(path.split('/')[4]);
          const login = await getLogin(env.DB, id, { withTokens: true });
          if (!login) return withCors(json({ error: 'not found' }, 404));
          return withCors(json({ login }));
        }

        if (path === '/api/internal/accounts/env' && req.method === 'GET') {
          // Return active social cookies for the pipeline runner (?user_id=N to scope).
          const uid = url.searchParams.get('user_id');
          const accounts = await listAccounts(env.DB, uid ? { id: Number(uid), is_admin: 0 } : null);
          const result = {};
          for (const a of accounts) {
            if (a.status !== 'active') continue;
            const row = await env.DB.prepare('SELECT cookies FROM accounts WHERE id = ?').bind(a.id).first();
            if (!row) continue;
            if (!result[a.platform]) result[a.platform] = [];
            let cookies;
            try { cookies = JSON.parse(row.cookies); } catch { cookies = row.cookies; }
            result[a.platform].push({ id: a.id, username: a.username, proxy: a.proxy || '', country: a.country || '', cookies });
          }
          return withCors(json(result));
        }

        if (path === '/api/internal/accounts/cookies' && req.method === 'GET') {
          // Return cookies for a specific platform (?user_id=N to scope).
          const platform = url.searchParams.get('platform') || '';
          const uid = url.searchParams.get('user_id');
          let cookies = null;
          if (uid) {
            const row = await env.DB
              .prepare("SELECT cookies FROM accounts WHERE platform = ? AND status = 'active' AND user_id = ? ORDER BY id DESC LIMIT 1")
              .bind(platform, Number(uid))
              .first();
            if (row) { try { cookies = JSON.parse(row.cookies); } catch { cookies = row.cookies; } }
          } else {
            cookies = await getAccountCookies(env.DB, platform);
          }
          if (!cookies) return withCors(json({ error: 'no cookies for platform' }, 404));
          return withCors(json({ platform, cookies }));
        }

        // Cookies by specific account ID
        const acctCookiesMatch = path.match(/^\/api\/internal\/accounts\/(\d+)\/cookies$/);
        if (acctCookiesMatch && req.method === 'GET') {
          const acctId = Number(acctCookiesMatch[1]);
          const row = await env.DB.prepare('SELECT cookies, username, proxy, country FROM accounts WHERE id = ?').bind(acctId).first();
          if (!row) return withCors(json({ error: 'account not found' }, 404));
          let cookies;
          try { cookies = JSON.parse(row.cookies); } catch { cookies = row.cookies; }
          return withCors(json({ id: acctId, cookies, username: row.username || '', proxy: row.proxy || '', country: row.country || '' }));
        }
      }

      return withCors(json({ error: 'not found' }, 404));
    } catch (e) {
      return withCors(json({ error: e.message || 'server error' }, 500));
    }
  },
};
