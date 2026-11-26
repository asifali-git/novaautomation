PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL DEFAULT 0,
  name TEXT NOT NULL,
  source_url TEXT NOT NULL,
  platform TEXT NOT NULL DEFAULT 'unknown',
  dest_provider TEXT NOT NULL DEFAULT 'youtube',
  login_id INTEGER,
  voice TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  baseline_key TEXT,
  last_seen_key TEXT,
  last_poll_at INTEGER,
  poll_failures INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  dest_platforms TEXT,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL DEFAULT 0,
  task_id INTEGER NOT NULL,
  video_key TEXT NOT NULL,
  url TEXT NOT NULL,
  title TEXT,
  status TEXT NOT NULL DEFAULT 'detected',
  file_path TEXT,
  processed_path TEXT,
  meta TEXT,
  upload_ref TEXT,
  error TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  duration REAL,
  dispatched_at INTEGER,
  detected_at INTEGER,
  downloaded_at INTEGER,
  edited_at INTEGER,
  uploaded_at INTEGER,
  failed_at INTEGER,
  cancelled_at INTEGER,
  retry_count INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS logins (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL DEFAULT 0,
  provider TEXT NOT NULL,
  label TEXT,
  channel_title TEXT,
  token_json TEXT NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_videos_task_key ON videos(task_id, video_key);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL DEFAULT 0,
  platform TEXT NOT NULL,
  username TEXT,
  cookies TEXT NOT NULL,
  proxy TEXT NOT NULL DEFAULT '',
  country TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS manual_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL DEFAULT 0,
  url TEXT NOT NULL,
  title TEXT,
  dest_platforms TEXT NOT NULL DEFAULT 'youtube',
  status TEXT NOT NULL DEFAULT 'queued',
  result TEXT,
  error TEXT,
  dispatched_at INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_manual_status ON manual_jobs(status);

-- Users (login accounts) + sessions
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL,
  email TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- OAuth flow: state -> initiating user (public callback needs attribution)
CREATE TABLE IF NOT EXISTS oauth_states (
  state TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  created_at INTEGER NOT NULL
);

-- Service API keys (rotation: lowest priority first) + per-user settings
CREATE TABLE IF NOT EXISTS api_keys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  service TEXT NOT NULL,
  label TEXT,
  value TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  status TEXT,
  last_error TEXT,
  last_used INTEGER,
  used_count INTEGER NOT NULL DEFAULT 0,
  checked_at INTEGER,
  meta TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_api_keys_scope ON api_keys(user_id, service, priority);

CREATE TABLE IF NOT EXISTS settings (
  user_id INTEGER NOT NULL,
  key TEXT NOT NULL,
  value TEXT,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (user_id, key)
);

-- Mobile APK: FCM device tokens (token rotates on reinstall/refresh).
CREATE TABLE IF NOT EXISTS device_tokens (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  token TEXT NOT NULL,
  device_id TEXT NOT NULL DEFAULT '',
  platform TEXT NOT NULL DEFAULT 'android',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE(token)
);
CREATE INDEX IF NOT EXISTS idx_devices_user ON device_tokens(user_id);

-- Pending video deliveries: CI uploads to Catbox -> worker queues a push.
-- status: pending -> delivered (APK acks)
CREATE TABLE IF NOT EXISTS deliveries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL DEFAULT 0,
  video_id INTEGER,
  task_id INTEGER,
  platform TEXT NOT NULL DEFAULT '',
  source_url TEXT,
  title TEXT,
  file_name TEXT NOT NULL,
  catbox_url TEXT NOT NULL,
  mime TEXT NOT NULL DEFAULT 'video/mp4',
  size_bytes INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  error TEXT,
  created_at INTEGER NOT NULL,
  delivered_at INTEGER,
  pushed_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_deliveries_user ON deliveries(user_id, status);
CREATE INDEX IF NOT EXISTS idx_deliveries_video ON deliveries(video_id);

-- Per-task delivery rules (browser/mobile se set hote hain).
-- send_to_app: 0=never send, 1=send to phone
-- app_file:    'raw' = original download, 'edited' = AI-edited, 'both' = dono
-- upload_social: 1 = upload to YouTube/FB/etc, 0 = download only (app ko dena hai)
CREATE TABLE IF NOT EXISTS task_delivery_rules (
  task_id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL DEFAULT 0,
  send_to_app INTEGER NOT NULL DEFAULT 1,
  app_file TEXT NOT NULL DEFAULT 'edited',
  upload_social INTEGER NOT NULL DEFAULT 1,
  updated_at INTEGER NOT NULL
);
