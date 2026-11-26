// FCM v1 sender for nova-hub (Cloudflare Workers).
//
// Secrets (set ONCE from the file that already exists in this project):
//   wrangler secret put FCM_SERVICE_ACCOUNT_JSON < firebase_service_account.json
//
// `firebase_service_account.json` already has project_id / client_email /
// private_key — exactly what this module needs. Workers cannot read local
// files, so the JSON has to live in a Worker secret (never in git).
//
// Why v1 and not the old legacy API: the legacy FCM endpoint is gone. v1 needs an
// OAuth2 access token, which we mint from the service account's private key using
// WebCrypto (available in Workers). No npm dependencies, nothing to bundle.
//
// The APK receives a DATA message (not just a notification) so it can wake up,
// enqueue WorkManager, and auto-download the Catbox file even while in background.

const OAUTH_URL = 'https://oauth2.googleapis.com/token';
const FCM_URL = (project) =>
  `https://fcm.googleapis.com/v1/projects/${encodeURIComponent(project)}/messages:send`;

function base64url(bytes) {
  const raw = String.fromCharCode(...new Uint8Array(bytes));
  return btoa(raw).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function pemToArrayBuffer(pem) {
  const clean = pem
    .replace(/-----BEGIN PRIVATE KEY-----/g, '')
    .replace(/-----END PRIVATE KEY-----/g, '')
    .replace(/\s+/g, '');
  const binary = atob(clean);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out.buffer;
}

async function serviceAccount(env) {
  // Project file -> Worker secret. Both common names are accepted.
  const raw = env.FCM_SERVICE_ACCOUNT_JSON || env.FIREBASE_SERVICE_ACCOUNT_JSON || '';
  if (!raw) return null;
  try {
    const sa = typeof raw === 'string' ? JSON.parse(raw) : raw;
    if (!sa.client_email || !sa.private_key || !sa.project_id) return null;
    return sa;
  } catch {
    return null;
  }
}

async function accessToken(sa) {
  const nowSec = Math.floor(Date.now() / 1000);
  const header = base64url(new TextEncoder().encode(JSON.stringify({ alg: 'RS256', typ: 'JWT' })));
  const claims = base64url(new TextEncoder().encode(JSON.stringify({
    iss: sa.client_email,
    scope: 'https://www.googleapis.com/auth/firebase.messaging',
    aud: OAUTH_URL,
    iat: nowSec,
    exp: nowSec + 3600,
  })));
  const unsigned = `${header}.${claims}`;

  const key = await crypto.subtle.importKey(
    'pkcs8',
    pemToArrayBuffer(sa.private_key),
    { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' },
    false,
    ['sign']
  );
  const sig = await crypto.subtle.sign(
    { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' },
    key,
    new TextEncoder().encode(unsigned)
  );
  const jwt = `${unsigned}.${base64url(sig)}`;

  const body = new URLSearchParams({
    grant_type: 'urn:ietf:params:oauth:grant-type:jwt-bearer',
    assertion: jwt,
  });
  const resp = await fetch(OAUTH_URL, {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  });
  if (!resp.ok) throw new Error(`oauth ${resp.status}: ${(await resp.text()).slice(0, 200)}`);
  const data = await resp.json();
  if (!data.access_token) throw new Error('oauth: no access_token');
  return data.access_token;
}

/**
 * Send one delivery to one device. Returns the FCM message id.
 * The APK handles the data payload itself (notification + WorkManager enqueue).
 */
export async function sendDeliveryPush(env, { token, delivery }) {
  const sa = await serviceAccount(env);
  if (!sa) return { ok: false, error: 'FCM_SERVICE_ACCOUNT_JSON not configured (see worker/src/fcm.js)' };
  if (!token) return { ok: false, error: 'no device token' };

  const access = await accessToken(sa);
  const payload = {
    message: {
      token,
      // Data payload: keys must be strings; the APK uses these to download.
      data: {
        type: 'nova_video',
        delivery_id: String(delivery.id || ''),
        video_id: String(delivery.video_id || ''),
        title: String(delivery.title || delivery.file_name || 'New video'),
        file_name: String(delivery.file_name || 'video.mp4'),
        catbox_url: String(delivery.catbox_url || ''),
        platform: String(delivery.platform || ''),
        size_bytes: String(delivery.size_bytes || 0),
        sent_at: String(Date.now()),
      },
      android: {
        priority: 'high',
        notification: {
          title: String(delivery.title || delivery.file_name || 'New video'),
          body: 'Tap to download · auto-save enabled',
          // alarm-like: the APK defines this channel with sound + vibration.
          channel_id: 'nova_video_alarm',
          notification_priority: 'PRIORITY_MAX',
          default_sound: true,
          default_vibrate_timings: true,
        },
      },
    },
  };

  const resp = await fetch(FCM_URL(sa.project_id), {
    method: 'POST',
    headers: {
      authorization: `Bearer ${access}`,
      'content-type': 'application/json',
    },
    body: JSON.stringify(payload),
  });
  const text = await resp.text();
  if (!resp.ok) return { ok: false, error: `fcm ${resp.status}: ${text.slice(0, 300)}` };
  try {
    return { ok: true, id: JSON.parse(text).name };
  } catch {
    return { ok: true, id: null };
  }
}

/** Push a delivery to every device registered by its owner. */
export async function pushDeliveryToUser(db, env, delivery) {
  const uid = Number(delivery.user_id || 0);
  if (!uid) return { sent: 0, error: 'no user_id' };
  const res = await db
    .prepare('SELECT token FROM device_tokens WHERE user_id = ?')
    .bind(uid)
    .all();
  const tokens = (res.results || []).map((r) => r.token).filter(Boolean);
  if (!tokens.length) return { sent: 0, error: 'no device registered' };

  let sent = 0;
  const errors = [];
  for (const token of tokens) {
    try {
      const out = await sendDeliveryPush(env, { token, delivery });
      if (out.ok) sent++;
      else errors.push(out.error || 'failed');
    } catch (exc) {
      errors.push(exc?.message || String(exc));
    }
  }
  return { sent, tokens: tokens.length, errors };
}
