-- Reset pipeline state: keep accounts (logins/accounts), links (tasks) and
-- user settings. Clear only video/delivery history and force re-detection.
DELETE FROM videos;
DELETE FROM deliveries;
DELETE FROM device_tokens;

-- Force the poller to treat every link as new again.
UPDATE tasks SET baseline_key = NULL, last_seen_key = NULL,
                 last_poll_at = NULL, poll_failures = 0, last_error = NULL;

-- Make sure downloads use the free yt-dlp path at best quality.
-- (settings are per-user; 0 = default/admin scope used by the CI runner)
INSERT INTO settings (user_id, key, value, updated_at)
VALUES (0, 'download_provider', 'ytdlp', strftime('%s','now')*1000),
       (0, 'video_quality', '1080', strftime('%s','now')*1000)
ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value,
                                        updated_at = excluded.updated_at;
