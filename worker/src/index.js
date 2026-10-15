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
    'access-control-allow-headers': 'content-type,x-token',
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

async function listTasks(db) {
  const res = await db.prepare('SELECT * FROM tasks ORDER BY id DESC').all();
  return res.results || [];
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
  const res = await db
    .prepare(
      `INSERT INTO tasks (name, source_url, platform, dest_provider, dest_platforms, login_id, status, created_at)
       VALUES (?, ?, ?, ?, ?, ?, 'active', ?)`
    )
    .bind(body.name || platform, url, platform, dest, destPlatforms, loginId, now())
    .run();
  return getTask(db, res.meta?.last_row_id);
}

async function patchTask(db, id, body) {
  const task = await getTask(db, id);
  if (!task) throw new Error('task not found');
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

async function listVideos(db, { status, limit = 100 } = {}) {
  if (status) {
    const res = await db
      .prepare('SELECT * FROM videos WHERE status = ? ORDER BY id DESC LIMIT ?')
      .bind(status, limit)
      .all();
    return res.results || [];
  }
  const res = await db.prepare('SELECT * FROM videos ORDER BY id DESC LIMIT ?').bind(limit).all();
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
  const t = now();
  const res = await db
    .prepare(
      `INSERT INTO videos (task_id, video_key, url, title, status, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'detected', ?, ?)`
    )
    .bind(taskId, key, url, body.title || null, t, t)
    .run();
  return { video: await getVideo(db, res.meta?.last_row_id), created: true };
}

const VIDEO_FIELDS = [
  'status', 'error', 'meta', 'upload_ref', 'file_path', 'processed_path', 'attempts', 'duration', 'title',
];

async function patchVideo(db, id, body) {
  const video = await getVideo(db, id);
  if (!video) throw new Error('video not found');
  const fields = [];
  const vals = [];
  for (const k of VIDEO_FIELDS) {
    if (k in body) {
      fields.push(`${k} = ?`);
      vals.push(typeof body[k] === 'object' ? JSON.stringify(body[k]) : body[k]);
    }
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

async function listLogins(db, { withTokens = false } = {}) {
  const res = await db.prepare('SELECT * FROM logins ORDER BY id DESC').all();
  const rows = res.results || [];
  return rows.map((r) => {
    const o = { id: r.id, provider: r.provider, label: r.label, channel_title: r.channel_title, created_at: r.created_at };
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
  const o = { id: r.id, provider: r.provider, label: r.label, channel_title: r.channel_title, created_at: r.created_at };
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
      `INSERT INTO logins (provider, label, channel_title, token_json, created_at)
       VALUES (?, ?, ?, ?, ?)`
    )
    .bind(body.provider || 'youtube', body.label || null, body.channel_title || null, tokenJson, now())
    .run();
  return getLogin(db, res.meta?.last_row_id, { withTokens: true });
}

async function deleteLogin(db, id) {
  const res = await db.prepare('DELETE FROM logins WHERE id = ?').bind(id).run();
  return res.meta?.changes > 0;
}

// --- Accounts (social media cookies) -----------------------------------------

async function listAccounts(db) {
  const res = await db.prepare('SELECT id, platform, username, status, created_at, updated_at FROM accounts ORDER BY id DESC').all();
  return res.results || [];
}

async function getAccount(db, id) {
  const r = await db.prepare('SELECT * FROM accounts WHERE id = ?').bind(id).first();
  return r;
}

async function saveAccount(db, body) {
  const platform = (body.platform || '').toLowerCase().trim();
  if (!platform) throw new Error('platform required');
  const cookies = body.cookies;
  if (!cookies) throw new Error('cookies required');
  const cookiesStr = typeof cookies === 'string' ? cookies : JSON.stringify(cookies);
  const username = body.username || null;
  const t = now();
  // Upsert: one account per platform (replace existing)
  const existing = await db.prepare('SELECT id FROM accounts WHERE platform = ?').bind(platform).first();
  if (existing) {
    await db.prepare('UPDATE accounts SET username = ?, cookies = ?, updated_at = ? WHERE id = ?')
      .bind(username, cookiesStr, t, existing.id).run();
    return getAccount(db, existing.id);
  }
  const res = await db.prepare('INSERT INTO accounts (platform, username, cookies, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)')
    .bind(platform, username, cookiesStr, 'active', t, t).run();
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

// --- Router -----------------------------------------------------------------

export default {
  async fetch(req, env) {
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
      // ---- Public API (frontend) ----
      if (path === '/api/state' && req.method === 'GET') {
        const [tasks, videos, logins, accounts] = await Promise.all([
          listTasks(env.DB),
          listVideos(env.DB, { limit: 200 }),
          listLogins(env.DB),
          listAccounts(env.DB),
        ]);
        return withCors(
          json({
            tasks,
            videos,
            logins,
            accounts,
            config: { has_client_id: !!env.YT_CLIENT_ID, has_client_secret: !!env.YT_CLIENT_SECRET },
          })
        );
      }

      // ---- Accounts (social cookies) ----
      if (path === '/api/accounts' && req.method === 'GET') {
        const accounts = await listAccounts(env.DB);
        return withCors(json({ accounts }));
      }

      if (path === '/api/accounts' && req.method === 'POST') {
        const body = await readBody(req);
        const account = await saveAccount(env.DB, body);
        return withCors(json({ account }, 201));
      }

      if (path.startsWith('/api/accounts/') && req.method === 'DELETE') {
        const id = Number(path.split('/')[3]);
        const ok = await deleteAccount(env.DB, id);
        return withCors(json({ ok }));
      }

      if (path === '/api/health' && req.method === 'GET') {
        return withCors(json({ ok: true, t: now() }));
      }

      if (path === '/api/tasks' && req.method === 'POST') {
        const body = await readBody(req);
        const task = await createTask(env.DB, body);
        return withCors(json({ task }, 201));
      }

      if (path.startsWith('/api/tasks/') && req.method === 'PATCH') {
        const id = Number(path.split('/')[3]);
        const body = await readBody(req);
        const task = await patchTask(env.DB, id, body);
        return withCors(json({ task }));
      }

      if (path.startsWith('/api/tasks/') && req.method === 'DELETE') {
        const id = Number(path.split('/')[3]);
        const ok = await deleteTask(env.DB, id);
        return withCors(json({ ok }));
      }

      if (path.startsWith('/api/logins/') && path.endsWith('/refresh-token') && req.method === 'GET') {
        const id = Number(path.split('/')[3]);
        const login = await getLogin(env.DB, id, { withTokens: true });
        if (!login) return withCors(json({ error: 'not found' }, 404));
        return withCors(json({ refresh_token: login.token?.refresh_token || null }));
      }

      if (path.startsWith('/api/logins/') && req.method === 'DELETE') {
        const id = Number(path.split('/')[3]);
        const ok = await deleteLogin(env.DB, id);
        return withCors(json({ ok }));
      }

      if (path === '/api/youtube/login/start' && req.method === 'POST') {
        const body = await readBody(req);
        const state = crypto.randomUUID();
        const auth_url = googleAuthUrl(env, state, body.client_id);
        return withCors(json({ auth_url, state }));
      }

      if (path === '/api/youtube/callback' && req.method === 'GET') {
        const code = url.searchParams.get('code');
        const state = url.searchParams.get('state') || '';
        const err = url.searchParams.get('error');
        const clientId = url.searchParams.get('client_id') || null;
        const clientSecret = url.searchParams.get('client_secret') || null;

        const html = (msg) =>
          new Response(
            `<!doctype html><html><head><meta charset="utf-8"><title>Nova</title>
             <style>body{font-family:system-ui;background:#0b0f17;color:#e6edf3;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
             .c{text-align:center}h1{font-size:20px}a{color:#4da3ff}</style></head>
             <body><div class="c"><h1>${msg}</h1><p>Window will close shortly.</p></div></body></html>`,
            { headers: { 'content-type': 'text/html; charset=utf-8' } }
          );

        if (err || !code) return html(`Login failed: ${err || 'no code'}`);
        try {
          const token = await exchangeCode(env, code, clientId, clientSecret);
          const channel = token.access_token ? await fetchChannelTitle(token.access_token) : null;
          const login = await saveLogin(env.DB, {
            provider: 'youtube',
            label: channel || 'YouTube channel',
            channel_title: channel,
            token,
          });
          // Signal the polling popup (via BroadcastChannel it can't use; instead the
          // frontend polls /api/state for a new login, so just confirm here).
          return html(`Login saved${channel ? ` — ${channel}` : ''} (id ${login.id})`);
        } catch (e) {
          return html(`Token exchange error: ${e.message}`);
        }
      }

      // ---- Internal API (CI runner) ----
      if (path.startsWith('/api/internal/')) {
        if (!checkInternal(req, env)) return withCors(json({ error: 'unauthorized' }, 401));

        if (path === '/api/internal/tasks' && req.method === 'GET') {
          const tasks = (await listTasks(env.DB)).filter((t) => t.status === 'active');
          return withCors(json({ tasks }));
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
          return withCors(json(result, result.created ? 201 : 200));
        }

        if (path.startsWith('/api/internal/videos/') && req.method === 'PATCH') {
          const id = Number(path.split('/')[4]);
          const body = await readBody(req);
          const video = await patchVideo(env.DB, id, body);
          return withCors(json({ video }));
        }

        if (path.startsWith('/api/internal/logins/') && req.method === 'GET') {
          const id = Number(path.split('/')[4]);
          const login = await getLogin(env.DB, id, { withTokens: true });
          if (!login) return withCors(json({ error: 'not found' }, 404));
          return withCors(json({ login }));
        }

        if (path === '/api/internal/logins/env' && req.method === 'GET') {
          // Return first usable YouTube login as YT_* env for the runner.
          const logins = await listLogins(env.DB, { withTokens: true });
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

        if (path === '/api/internal/accounts/env' && req.method === 'GET') {
          // Return all active social cookies for the pipeline runner.
          const accounts = await listAccounts(env.DB);
          const result = {};
          for (const a of accounts) {
            if (a.status !== 'active') continue;
            const full = await getAccount(env.DB, a.id);
            try {
              result[a.platform] = { username: a.username, cookies: JSON.parse(full.cookies) };
            } catch {
              result[a.platform] = { username: a.username, cookies: full.cookies };
            }
          }
          return withCors(json(result));
        }

        if (path === '/api/internal/accounts/cookies' && req.method === 'GET') {
          // Return cookies for a specific platform.
          const platform = url.searchParams.get('platform') || '';
          const cookies = await getAccountCookies(env.DB, platform);
          if (!cookies) return withCors(json({ error: 'no cookies for platform' }, 404));
          return withCors(json({ platform, cookies }));
        }
      }

      return withCors(json({ error: 'not found' }, 404));
    } catch (e) {
      return withCors(json({ error: e.message || 'server error' }, 500));
    }
  },
};
