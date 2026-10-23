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
