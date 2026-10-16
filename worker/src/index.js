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
      .prepare(
        `SELECT v.*, t.name AS source_name, t.source_url AS source
         FROM videos v LEFT JOIN tasks t ON v.task_id = t.id
         WHERE v.status = ? ORDER BY v.id DESC LIMIT ?`
      )
      .bind(status, limit)
      .all();
    return res.results || [];
  }
  const res = await db
    .prepare(
      `SELECT v.*, t.name AS source_name, t.source_url AS source
       FROM videos v LEFT JOIN tasks t ON v.task_id = t.id
       ORDER BY v.id DESC LIMIT ?`
    )
    .bind(limit)
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
  const res = await db.prepare('SELECT id, platform, username, proxy, country, status, created_at, updated_at FROM accounts ORDER BY id DESC').all();
  return res.results || [];
}

async function getAccount(db, id) {
  const r = await db.prepare('SELECT id, platform, username, proxy, country, status, created_at, updated_at FROM accounts WHERE id = ?').bind(id).first();
  return r;
}

async function saveAccount(db, body) {
  const platform = (body.platform || '').toLowerCase().trim();
  if (!platform) throw new Error('platform required');
  const cookies = body.cookies;
  if (!cookies) throw new Error('cookies required');
  const cookiesStr = typeof cookies === 'string' ? cookies : JSON.stringify(cookies);
  const username = body.username || null;
  const proxy = (body.proxy || '').trim();
  const country = (body.country || '').trim();
  const t = now();
  // Upsert: per platform + username (allows multiple accounts per platform)
  const existing = username
    ? await db.prepare('SELECT id FROM accounts WHERE platform = ? AND username = ?').bind(platform, username).first()
    : await db.prepare('SELECT id FROM accounts WHERE platform = ? AND (username IS NULL OR username = ?)').bind(platform, '').first();
  if (existing) {
    await db.prepare('UPDATE accounts SET username = ?, cookies = ?, proxy = ?, country = ?, updated_at = ? WHERE id = ?')
      .bind(username, cookiesStr, proxy, country, t, existing.id).run();
    return getAccount(db, existing.id);
  }
  const res = await db.prepare('INSERT INTO accounts (platform, username, cookies, proxy, country, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)')
    .bind(platform, username, cookiesStr, proxy, country, 'active', t, t).run();
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

      // ---- Manual upload jobs ----
      if (path === '/api/manual/upload' && req.method === 'POST') {
        const body = await readBody(req);
        const url = (body.url || '').trim();
        if (!url) return withCors(json({ error: 'url required' }, 400));
        const dest = Array.isArray(body.dest_platforms) ? body.dest_platforms.join(',') : (body.dest_platforms || 'youtube');
        const res = await env.DB
          .prepare('INSERT INTO manual_jobs (url, title, dest_platforms, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)')
          .bind(url, body.title || null, dest, 'queued', now(), now())
          .run();
        const job = await env.DB.prepare('SELECT * FROM manual_jobs WHERE id = ?').bind(res.meta?.last_row_id).first();
        return withCors(json({ job }, 201));
      }

      if (path === '/api/manual/jobs' && req.method === 'GET') {
        const res = await env.DB.prepare('SELECT * FROM manual_jobs ORDER BY id DESC LIMIT 50').all();
        return withCors(json({ jobs: res.results || [] }));
      }

      if (path.startsWith('/api/manual/jobs/') && req.method === 'DELETE') {
        const id = Number(path.split('/')[4]);
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

        // Redirect back to the app (same tab flow) instead of dead-end HTML.
        const appBase = (env.ALLOWED_ORIGIN && env.ALLOWED_ORIGIN !== '*')
          ? env.ALLOWED_ORIGIN
          : 'https://novaautomation.pages.dev';
        const redirectTo = (params, hash = 'youtube') =>
          Response.redirect(`${appBase}/?${params}#${hash}`, 302);

        if (err || !code) {
          return redirectTo(`yt=error&msg=${encodeURIComponent(err || 'no code')}`);
        }
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
          return withCors(json(result, result.created ? 201 : 200));
        }

        if (path.startsWith('/api/internal/videos/') && req.method === 'PATCH') {
          const id = Number(path.split('/')[4]);
          const body = await readBody(req);
          const video = await patchVideo(env.DB, id, body);
          return withCors(json({ video }));
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

        if (/^\/api\/internal\/logins\/\d+$/.test(path) && req.method === 'GET') {
          const id = Number(path.split('/')[4]);
          const login = await getLogin(env.DB, id, { withTokens: true });
          if (!login) return withCors(json({ error: 'not found' }, 404));
          return withCors(json({ login }));
        }

        if (path === '/api/internal/accounts/env' && req.method === 'GET') {
          // Return all active social cookies for the pipeline runner.
          const accounts = await listAccounts(env.DB);
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
          // Return cookies for a specific platform.
          const platform = url.searchParams.get('platform') || '';
          const cookies = await getAccountCookies(env.DB, platform);
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
