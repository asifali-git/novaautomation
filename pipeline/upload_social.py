"""Social media upload via browser cookies (no API keys).

Uploads the final edited video to TikTok, Instagram, and Facebook
using Selenium + undetected-chromedriver with cookies stored in Worker D1.

Cookies are fetched from Worker API (/api/internal/accounts/env).
Per-source platform selection comes from task's dest_platforms field.

Env:
  WORKER_URL, INTERNAL_TOKEN   worker API access
  VIDEO_DEST_PLATFORMS         comma-separated: tiktok,instagram,facebook
                               (from task.dest_platforms; empty = none)
"""
import os
import sys
import json
import time
import base64
import pickle
import random
import tempfile
import atexit

import requests

WORKER = os.getenv("WORKER_URL", "").rstrip("/")
TOKEN = os.getenv("INTERNAL_TOKEN", "")

# Cache for cookies fetched from worker
_cookies_cache = None


def _fetch_all_cookies():
    """Fetch all social cookies from Worker (scoped to PIPELINE_USER_ID if set)."""
    global _cookies_cache
    if _cookies_cache is not None:
        return _cookies_cache
    if not WORKER or not TOKEN:
        print("[social] WORKER_URL/INTERNAL_TOKEN not set")
        _cookies_cache = {}
        return _cookies_cache
    try:
        uid = os.getenv("PIPELINE_USER_ID", "").strip()
        params = f"?user_id={uid}" if uid else ""
        r = requests.get(
            f"{WORKER}/api/internal/accounts/env{params}",
            headers={"x-token": TOKEN},
            timeout=30,
        )
        r.raise_for_status()
        _cookies_cache = r.json()
    except Exception as e:
        print(f"[social] failed to fetch cookies: {e}")
        _cookies_cache = {}
    return _cookies_cache


def _get_cookies(platform, account_id=None):
    """Get cookies for a specific platform from worker.
    If account_id is given, fetch that specific account's cookies.
    Otherwise return the first active account's cookies for the platform."""
    if account_id and WORKER and TOKEN:
        try:
            r = requests.get(
                f"{WORKER}/api/internal/accounts/{account_id}/cookies",
                headers={"x-token": TOKEN},
                timeout=30,
            )
            r.raise_for_status()
            return r.json().get("cookies")
        except Exception as e:
            print(f"[social] failed to fetch account {account_id} cookies: {e}")
            return None

    all_cookies = _fetch_all_cookies()
    entry = all_cookies.get(platform)
    if not entry:
        return None
    # New format: list of accounts
    if isinstance(entry, list):
        if not entry:
            return None
        return entry[0].get("cookies")
    # Old format: single dict
    if isinstance(entry, dict):
        return entry.get("cookies")
    return None


def _get_accounts_for_platform(platform):
    """Get list of accounts for a platform from worker env endpoint."""
    all_cookies = _fetch_all_cookies()
    entry = all_cookies.get(platform)
    if not entry:
        return []
    if isinstance(entry, list):
        return entry
    if isinstance(entry, dict):
        return [entry]
    return []


def _get_enabled_platforms():
    """Get platforms to upload to from env (set by pipeline from task.dest_platforms).
    Returns list of (platform, account_id_or_None) tuples."""
    raw = os.getenv("VIDEO_DEST_PLATFORMS", "")
    if not raw:
        return []
    result = []
    for part in raw.split(","):
        part = part.strip().lower()
        if not part:
            continue
        if ":" in part:
            plat, acct = part.split(":", 1)
            result.append((plat, acct or None))
        else:
            result.append((part, None))
    return result


def _fb_page_sel(acct_id):
    """facebook:<acct_id> -> Graph page id for upload_fb (None = default page).

    acct_id may be a registry page id, a page name, or a worker accounts
    row id (API-backed page rows carry the page name as username)."""
    if not acct_id:
        return None
    try:
        import upload_fb
        pages = upload_fb.fb_registry()["pages"]
    except Exception:
        return None
    s = str(acct_id).strip()
    sl = s.casefold()
    for p in pages:
        if p.get("id") == s or (p.get("name") or "").casefold() == sl:
            return p.get("id")
    try:
        uname = (_account_meta(acct_id).get("username") or "").strip().casefold()
    except Exception:
        uname = ""
    if uname:
        for p in pages:
            if (p.get("name") or "").strip().casefold() == uname:
                return p.get("id")
    print(f"[social] facebook account {s!r} matches no API page — "
          f"using default page", file=sys.stderr)
    return None


def _up(success, url=None, error=None, account_id=None):
    """Uniform uploader return value: {success, url, account, error?}."""
    r = {"success": bool(success), "url": url or None, "account": account_id}
    if error:
        r["error"] = str(error)[:300]
    return r


_account_meta_cache = {}


def _account_meta(account_id):
    """Fetch username/proxy/country for a specific account (cached, best-effort)."""
    if not account_id:
        return {}
    key = str(account_id)
    if key in _account_meta_cache:
        return _account_meta_cache[key]
    meta = {}
    if WORKER and TOKEN:
        try:
            r = requests.get(
                f"{WORKER}/api/internal/accounts/{account_id}/cookies",
                headers={"x-token": TOKEN},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json() or {}
            meta = {k: data.get(k) for k in ("username", "proxy", "country") if data.get(k)}
        except Exception as e:
            print(f"[social] failed to fetch account {account_id} meta: {e}")
    _account_meta_cache[key] = meta
    return meta


# ─────────────────── PROXY + COUNTRY FINGERPRINT ───────────────────

# country code/alias -> browser profile (timezone, language, geolocation)
COUNTRY_PROFILES = {
    "GB": {"tz": "Europe/London", "lang": "en-GB", "accept": "en-GB,en;q=0.9",
           "geo": (51.5072, -0.1276), "languages": ["en-GB", "en"]},
    "DE": {"tz": "Europe/Berlin", "lang": "de-DE", "accept": "de-DE,de;q=0.9,en;q=0.5",
           "geo": (52.5200, 13.4050), "languages": ["de-DE", "de", "en"]},
    "IT": {"tz": "Europe/Rome", "lang": "it-IT", "accept": "it-IT,it;q=0.9,en;q=0.5",
           "geo": (41.9028, 12.4964), "languages": ["it-IT", "it", "en"]},
    "FR": {"tz": "Europe/Paris", "lang": "fr-FR", "accept": "fr-FR,fr;q=0.9,en;q=0.5",
           "geo": (48.8566, 2.3522), "languages": ["fr-FR", "fr", "en"]},
    "US": {"tz": "America/New_York", "lang": "en-US", "accept": "en-US,en;q=0.9",
           "geo": (40.7128, -74.0060), "languages": ["en-US", "en"]},
    "NL": {"tz": "Europe/Amsterdam", "lang": "nl-NL", "accept": "nl-NL,nl;q=0.9,en;q=0.5",
           "geo": (52.3676, 4.9041), "languages": ["nl-NL", "nl", "en"]},
    "ES": {"tz": "Europe/Madrid", "lang": "es-ES", "accept": "es-ES,es;q=0.9,en;q=0.5",
           "geo": (40.4168, -3.7038), "languages": ["es-ES", "es", "en"]},
    "IN": {"tz": "Asia/Kolkata", "lang": "en-IN", "accept": "en-IN,en;q=0.9",
           "geo": (19.0760, 72.8777), "languages": ["en-IN", "en"]},
}

_COUNTRY_ALIASES = {
    "uk": "GB", "united kingdom": "GB", "great britain": "GB", "england": "GB",
    "germany": "DE", "deutschland": "DE",
    "italy": "IT", "italia": "IT",
    "france": "FR",
    "usa": "US", "united states": "US", "america": "US",
    "netherlands": "NL", "holland": "NL",
    "spain": "ES", "espana": "ES",
    "india": "IN",
}


def _country_profile(country):
    """Resolve a country string (code or name) to a COUNTRY_PROFILES entry."""
    if not country:
        return None
    c = str(country).strip()
    key = c.upper() if len(c) == 2 and c.isalpha() else c.lower()
    code = key if key in COUNTRY_PROFILES else _COUNTRY_ALIASES.get(key)
    if not code:
        # try alias for 2-letter input too
        code = _COUNTRY_ALIASES.get(key.lower())
    return COUNTRY_PROFILES.get(code) if code else None


def _normalize_proxy(proxy):
    """Parse proxy string -> (chrome_proxy_server, (user, pass) or None).

    Accepts: host:port | user:pass@host:port | http://user:pass@host:port |
    socks5://host:port
    """
    if not proxy or not str(proxy).strip():
        return None, None
    from urllib.parse import urlparse

    p = str(proxy).strip()
    if "://" not in p:
        p = "http://" + p
    try:
        u = urlparse(p)
    except Exception:
        return None, None
    if not u.hostname:
        return None, None
    auth = (u.username, u.password) if u.username else None
    scheme = u.scheme if u.scheme in ("http", "https", "socks5", "socks4") else "http"
    port = u.port or (443 if scheme == "https" else 80)
    return f"{scheme}://{u.hostname}:{port}", auth


class _ProxyRelay:
    """Local proxy relay: Chrome connects here (no auth) and we forward to the
    upstream authenticated proxy. Chrome cannot send proxy credentials itself."""

    def __init__(self, upstream_server, auth):
        import threading
        from urllib.parse import urlparse

        self.upstream = urlparse(upstream_server)
        self.auth = auth
        self._sock = None
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self.port = None

    def start(self):
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        s.listen(64)
        self._sock = s
        self.port = s.getsockname()[1]
        self._thread.start()
        return self.port

    def stop(self):
        self._stop = True
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass

    @property
    def chrome_url(self):
        return f"http://127.0.0.1:{self.port}"

    def _proxy_auth_header(self):
        if not self.auth:
            return ""
        import base64

        cred = base64.b64encode(
            f"{self.auth[0]}:{self.auth[1] or ''}".encode()
        ).decode()
        return f"Proxy-Authorization: Basic {cred}\r\n"

    def _serve(self):
        import socket
        import threading

        while not self._stop:
            try:
                client, _ = self._sock.accept()
            except Exception:
                break
            threading.Thread(
                target=self._handle_client, args=(client,), daemon=True
            ).start()

    def _handle_client(self, client):
        import socket

        try:
            client.settimeout(60)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = client.recv(65536)
                if not chunk:
                    client.close()
                    return
                data += chunk
                if len(data) > 65536:
                    client.close()
                    return
            head, _, rest = data.partition(b"\r\n\r\n")
            lines = head.split(b"\r\n")
            request_line = lines[0].decode("latin-1", "replace")
            parts = request_line.split(" ")
            if len(parts) < 3:
                client.close()
                return
            method, target = parts[0], parts[1]

            upstream = socket.create_connection(
                (self.upstream.hostname, self.upstream.port or 8080), timeout=30
            )
            upstream.settimeout(60)

            if method == "CONNECT":
                auth = self._proxy_auth_header()
                upstream.sendall(
                    f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n{auth}\r\n".encode()
                )
                resp = b""
                while b"\r\n\r\n" not in resp:
                    chunk = upstream.recv(4096)
                    if not chunk:
                        break
                    resp += chunk
                status = resp.split(b" ", 2)[1] if len(resp.split(b" ", 2)) > 2 else b"000"
                if not status.startswith(b"2"):
                    client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                    client.close()
                    upstream.close()
                    return
                head_end = resp.find(b"\r\n\r\n")
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                leftover = resp[head_end + 4:] if head_end >= 0 else b""
                if leftover:
                    client.sendall(leftover)
                self._pump(client, upstream)
            else:
                # Plain HTTP — forward to upstream proxy with absolute URI.
                host_hdr = ""
                for ln in lines[1:]:
                    if ln.lower().startswith(b"host:"):
                        host_hdr = ln.decode("latin-1", "replace")
                auth = self._proxy_auth_header()
                out = [request_line]
                skip = {b"proxy-authorization", b"proxy-connection"}
                for ln in lines[1:]:
                    if ln.split(b":")[0].lower() in skip:
                        continue
                    out.append(ln.decode("latin-1", "replace"))
                if auth:
                    out.append(auth.strip())
                req = ("\r\n".join(out) + "\r\n\r\n").encode() + rest
                upstream.sendall(req)
                while True:
                    chunk = upstream.recv(65536)
                    if not chunk:
                        break
                    client.sendall(chunk)
                upstream.close()
                client.close()
        except Exception:
            pass
        finally:
            try:
                client.close()
            except Exception:
                pass

    @staticmethod
    def _pump(a, b):
        import select
        import socket

        sockets = [a, b]
        try:
            while True:
                r, _, x = select.select(sockets, [], sockets, 60)
                if x or not r:
                    break
                for s in r:
                    data = s.recv(65536)
                    if not data:
                        return
                    (b if s is a else a).sendall(data)
        except Exception:
            pass
        finally:
            for s in (a, b):
                try:
                    s.close()
                except Exception:
                    pass



def _parse_cookies_any(data):
    """Parse cookies from any format: JSON list, JSON dict, Netscape txt, or pickle bytes.
    Returns list of cookie dicts suitable for Selenium add_cookie()."""
    # Already a list of dicts
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data
    # Dict with __base64 pickle
    if isinstance(data, dict) and "__base64" in data:
        try:
            raw = base64.b64decode(data["__base64"])
            try:
                loaded = pickle.loads(raw)
                if isinstance(loaded, list):
                    return loaded
            except Exception:
                pass
            # Try JSON from base64
            try:
                loaded = json.loads(raw.decode("utf-8"))
                if isinstance(loaded, list):
                    return loaded
            except Exception:
                pass
            # Try Netscape from base64
            text = raw.decode("utf-8", errors="replace")
            if "\t" in text:
                return _parse_netscape(text)
        except Exception:
            pass
        return None
    # Dict (single cookie or wrapper)
    if isinstance(data, dict):
        # Could be {"cookies": [...]} or a single cookie dict
        if "cookies" in data and isinstance(data["cookies"], list):
            return data["cookies"]
        # Single cookie dict with name/value
        if "name" in data and "value" in data:
            return [data]
        return None
    # String: JSON or Netscape
    if isinstance(data, str):
        s = data.strip()
        if s.startswith("[") or s.startswith("{"):
            try:
                loaded = json.loads(s)
                if isinstance(loaded, list):
                    return loaded
                if isinstance(loaded, dict) and "cookies" in loaded:
                    return loaded["cookies"]
            except Exception:
                pass
        if "\t" in s:
            return _parse_netscape(s)
    # Raw bytes: try pickle, then JSON, then Netscape
    if isinstance(data, bytes):
        try:
            loaded = pickle.loads(data)
            if isinstance(loaded, list):
                return loaded
        except Exception:
            pass
        try:
            loaded = json.loads(data.decode("utf-8"))
            if isinstance(loaded, list):
                return loaded
        except Exception:
            pass
        try:
            text = data.decode("utf-8", errors="replace")
            if "\t" in text:
                return _parse_netscape(text)
        except Exception:
            pass
    return None


def _parse_netscape(text):
    """Parse Netscape cookie file format to list of Selenium cookie dicts."""
    cookies = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, flag, path, secure, expiry, name, value = parts[:7]
        c = {
            "name": name,
            "value": value,
            "domain": domain.lstrip("."),
            "path": path or "/",
            "secure": secure.upper() == "TRUE",
        }
        try:
            c["expiry"] = int(float(expiry))
        except Exception:
            pass
        cookies.append(c)
    return cookies if cookies else None


def _load_cookies_for_driver(driver, cookies):
    """Load any cookie format into Selenium driver. Returns count loaded."""
    parsed = _parse_cookies_any(cookies)
    if not parsed:
        print("[social] could not parse cookies (unknown format)")
        return 0
    loaded = 0
    for c in parsed:
        try:
            # Normalize field names for Selenium
            clean = {}
            for k in ("name", "value", "domain", "path", "secure", "httpOnly"):
                if k in c and c[k] is not None:
                    clean[k] = c[k]
            # expiry: Selenium expects "expiry" (seconds since epoch)
            if "expiry" in c and c["expiry"] is not None:
                try:
                    clean["expiry"] = int(float(c["expiry"]))
                except Exception:
                    pass
            elif "expires" in c and c["expires"] is not None:
                try:
                    exp = int(float(c["expires"]))
                    if exp > 0:
                        clean["expiry"] = exp
                except Exception:
                    pass
            # sameSite: Selenium expects "same_site" with lowercase values
            ss = c.get("sameSite") or c.get("same_site")
            if ss:
                ss_lower = str(ss).lower()
                if ss_lower in ("strict", "lax", "none"):
                    clean["same_site"] = ss_lower
            if "name" not in clean or "value" not in clean:
                continue
            driver.add_cookie(clean)
            loaded += 1
        except Exception as e:
            # Debug: show which cookie failed
            cname = c.get("name", "?")
            print(f"[social] cookie '{cname}' failed: {e}")
    print(f"[social] loaded {loaded}/{len(parsed)} cookies into browser")
    return loaded


def _sleep(lo=1.0, hi=3.0):
    time.sleep(random.uniform(lo, hi))


def _spoof(driver, languages=None):
    langs = languages or ["en-US", "en"]
    langs_js = json.dumps(langs)
    driver.execute_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
    )
    driver.execute_script(
        "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3]})"
    )
    driver.execute_script(
        f"Object.defineProperty(navigator,'languages',{{get:()=>{langs_js}}})"
    )


def _decode_cookies_b64(env_name):
    """Decode base64 cookie blob to a temp file path. Returns path or None."""
    raw = os.getenv(env_name, "")
    if not raw:
        return None
    data = base64.b64decode(raw)
    suffix = ".json" if "FACEBOOK" in env_name else ".pkl"
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


_xvfb_proc = None


def _start_xvfb():
    """Start a virtual X display (Xvfb) on Linux so Chrome runs HEADED.

    Headless Chrome is detectable (permissions/notification API fingerprints) —
    a headed browser on a virtual display looks like a real user session.
    Returns the DISPLAY string, or None to fall back to --headless."""
    global _xvfb_proc
    if sys.platform != "linux":
        return None
    if os.environ.get("DISPLAY"):
        return os.environ["DISPLAY"]
    import shutil
    import subprocess

    if _xvfb_proc is not None and _xvfb_proc.poll() is None:
        return os.environ.get("DISPLAY")
    if not shutil.which("Xvfb"):
        print("[social] Xvfb not installed; falling back to headless")
        return None
    for disp in (":99", ":98", ":97"):
        try:
            proc = subprocess.Popen(
                ["Xvfb", disp, "-screen", "0", "1280x900x24", "-nolisten", "tcp"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            print(f"[social] Xvfb spawn failed: {e}")
            continue
        time.sleep(1.0)
        if proc.poll() is None:
            _xvfb_proc = proc
            os.environ["DISPLAY"] = disp
            print(f"[social] Xvfb virtual display up (DISPLAY={disp}) — headed Chrome")
            return disp
    print("[social] Xvfb could not grab a display; falling back to headless")
    return None


def _stop_xvfb():
    global _xvfb_proc
    if _xvfb_proc is not None:
        try:
            _xvfb_proc.terminate()
        except Exception:
            pass
        _xvfb_proc = None
    os.environ.pop("DISPLAY", None)


atexit.register(_stop_xvfb)


def _browser_profile_dir(platform, account_id=None):
    """Persistent Chrome user-data-dir for a platform/account.

    Real logins (cookies, localStorage, fingerprint) persist across runs so
    automation reuses ONE 'device' instead of a fresh browser every upload
    (a fresh profile per post is a strong bot signal). Override with env
    <PLATFORM>_PROFILE_DIR; default work/browser_profiles/<platform>[_id].
    NOTE: only meaningful on a persistent machine — GH Actions runners are
    ephemeral and lose the profile after each job."""
    env_key = f"{platform.upper()}_PROFILE_DIR"
    base = os.environ.get(env_key)
    if base:
        d = base
    else:
        suffix = f"{platform}_{account_id}" if account_id else platform
        d = os.path.join(os.getcwd(), "work", "browser_profiles", suffix)
    os.makedirs(d, exist_ok=True)
    return os.path.abspath(d)


def _make_driver(headless=True, proxy=None, country=None, profile_dir=None):
    """Create driver. proxy: raw proxy string (may include user:pass).
    country: code/name — sets timezone/locale/geolocation/UA-language.
    headless=True tries Xvfb first (headed on virtual display); real
    --headless only as fallback. profile_dir: persistent Chrome user-data-dir
    (real login persists across runs — anti-bot)."""
    import undetected_chromedriver as uc

    profile = _country_profile(country)
    server, auth = _normalize_proxy(proxy)
    relay = None

    opts = uc.ChromeOptions()
    if headless and not _start_xvfb():
        opts.add_argument("--headless=new")
        print("[social] Chrome: --headless=new (Xvfb unavailable)")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")

    if profile_dir:
        os.makedirs(profile_dir, exist_ok=True)
        opts.add_argument(f"--user-data-dir={os.path.abspath(profile_dir)}")
        opts.add_argument("--profile-directory=Default")
        print(f"[social] persistent profile: {profile_dir}")

    if profile:
        opts.add_argument(f"--lang={profile['lang']}")
        # WebRTC must never leak the real IP when a proxy is in use.
        opts.add_argument("--force-webrtc-ip-handling-policy=disable_non_proxied_udp")
        opts.add_argument("--webrtc-ip-handling-policy=disable_non_proxied_udp")

    if server:
        if auth:
            # Chrome cannot authenticate to a proxy itself — run a local relay.
            relay = _ProxyRelay(server, auth)
            relay.start()
            opts.add_argument(f"--proxy-server={relay.chrome_url}")
            print(f"[social] proxy relay on {relay.chrome_url} -> {server}")
        else:
            opts.add_argument(f"--proxy-server={server}")
            print(f"[social] using proxy {server}")
        # Never bypass the proxy for anything (avoid IP leaks).
        opts.add_argument("--proxy-bypass-list=<-loopback>")

    driver = uc.Chrome(options=opts)
    if relay:
        driver._nova_proxy_relay = relay

    # CDP overrides: timezone, locale, geolocation, Accept-Language.
    if profile:
        try:
            driver.execute_cdp_cmd(
                "Emulation.setTimezoneOverride", {"timezoneId": profile["tz"]}
            )
            print(f"[social] timezone -> {profile['tz']}")
        except Exception as e:
            print(f"[social] timezone override failed: {e}")
        try:
            driver.execute_cdp_cmd(
                "Emulation.setLocaleOverride", {"locale": profile["lang"]}
            )
        except Exception as e:
            print(f"[social] locale override failed: {e}")
        try:
            lat, lon = profile["geo"]
            driver.execute_cdp_cmd(
                "Emulation.setGeolocationOverride",
                {"latitude": lat, "longitude": lon, "accuracy": 50},
            )
            driver.execute_cdp_cmd(
                "Browser.grantPermissions",
                {"permissions": ["geolocation"]},
            )
        except Exception as e:
            print(f"[social] geolocation override failed: {e}")
        try:
            driver.execute_cdp_cmd(
                "Network.setUserAgentOverride",
                {"acceptLanguage": profile["accept"]},
            )
        except Exception as e:
            print(f"[social] UA language override failed: {e}")
    return driver


def _quit(driver):
    """Quit driver and stop any local proxy relay attached to it."""
    try:
        relay = getattr(driver, "_nova_proxy_relay", None)
        if relay:
            relay.stop()
    except Exception:
        pass
    try:
        driver.quit()
    except Exception:
        pass


# ─────────────────────────── TIKTOK ───────────────────────────

def _tiktok_logged_in(driver):
    """True if the driver (persistent profile or injected cookies) has a
    TikTok session."""
    try:
        names = {c.get("name") for c in driver.get_cookies()}
        return bool(names & {"sessionid", "sid_tt", "sidguard"})
    except Exception:
        return False


def _tiktok_warmup(driver, languages=None, scrolls=None):
    """Light organic For You feed activity before upload.

    Going straight to the upload page from a cold browser looks robotic.
    A short human-like browse (random mouse moves + scrolls + pauses) warms
    the session. Disable with TIKTOK_WARMUP=0."""
    if os.environ.get("TIKTOK_WARMUP", "1") == "0":
        print("[tiktok] warmup disabled (TIKTOK_WARMUP=0)")
        return
    try:
        driver.get("https://www.tiktok.com/foryou")
        _spoof(driver, languages=languages)
        _sleep(4, 7)
        n = scrolls or random.randint(3, 6)
        from selenium.webdriver.common.action_chains import ActionChains

        for _ in range(n):
            try:
                size = driver.get_window_size()
                ActionChains(driver).move_by_offset(
                    random.randint(-120, 120), random.randint(-80, 80)
                ).move_by_offset(
                    random.randint(-size["width"] // 5, size["width"] // 5),
                    random.randint(-size["height"] // 5, size["height"] // 5),
                ).perform()
            except Exception:
                pass
            driver.execute_script(
                f"window.scrollBy(0, {random.randint(300, 900)});"
            )
            _sleep(4, 10)
        print(f"[tiktok] feed warmup done ({n} scrolls)")
    except Exception as e:
        print(f"[tiktok] warmup skipped: {e}")


def login_tiktok(account_id=None):
    """Open a HEADED browser on the persistent TikTok profile for a one-time
    MANUAL login. The session is saved in the profile on disk and reused by
    every future automated upload (no cookie re-injection, same device)."""
    profile_dir = _browser_profile_dir("tiktok", account_id)
    print(f"[tiktok] login profile: {profile_dir}")
    print("[tiktok] log in manually in the opened window (waiting up to 10 min)...")
    driver = _make_driver(headless=False, profile_dir=profile_dir)
    try:
        driver.get("https://www.tiktok.com/login")
        deadline = time.time() + 600
        while time.time() < deadline:
            if _tiktok_logged_in(driver):
                print("[tiktok] login detected — session saved to profile")
                time.sleep(5)  # let Chrome flush the profile to disk
                return True
            time.sleep(3)
        print("[tiktok] login timed out (10 min)")
        return False
    finally:
        _quit(driver)


def _harvest_url_js(driver, pattern):
    """Return first anchor href on page matching regex pattern.
    Fallback: regex over raw innerHTML (handles dialogs rendered from JS data)."""
    import re

    rx = re.compile(pattern, re.I)
    try:
        hrefs = driver.execute_script(
            """
            const out = [];
            document.querySelectorAll('a[href]').forEach(a => {
                try { out.push(a.href); } catch(e) {}
            });
            return out;
            """,
        )
    except Exception:
        hrefs = None
    seen = set()
    for h in hrefs or []:
        if h in seen:
            continue
        seen.add(h)
        if rx.search(h):
            return h
    try:
        html = driver.execute_script("return document.documentElement.innerHTML") or ""
        m = rx.search(html)
        if m:
            found = m.group(0)
            if found.startswith("/"):
                parts = driver.current_url.split("/")
                found = f"{parts[0]}//{parts[2]}" + found
            return found
    except Exception:
        pass
    return None


def _harvest_profile_url(driver, pattern, url, timeout=45):
    """Navigate to profile page and return first anchor href matching pattern."""
    import re

    try:
        driver.get(url)
        _sleep(4, 6)
        deadline = time.time() + timeout
        rx = re.compile(pattern, re.I)
        while time.time() < deadline:
            try:
                hrefs = driver.execute_script(
                    "return [...document.querySelectorAll('a[href]')].map(a=>a.href)"
                ) or []
                for h in hrefs:
                    if rx.search(h):
                        return h
            except Exception:
                pass
            _sleep(2, 3)
    except Exception as e:
        print(f"[social] profile harvest failed: {e}")
    return None


def _page_hint(driver):
    """Compact on-page diagnosis: upload %, visible overlays, body text head."""
    try:
        return driver.execute_script("""
            const body = (document.body && document.body.innerText || '').replace(/\\s+/g, ' ').trim();
            const pct = [...document.querySelectorAll('div,span,p')]
              .map(e => (e.childElementCount === 0 ? (e.textContent || '') : '').trim())
              .find(s => /^\\d{1,3}%$/.test(s)) || '';
            const ov = [...document.querySelectorAll('[class*="overlay" i],[class*="Overlay"],[class*="Modal"],[class*="Dialog"]')]
              .filter(e => { const s = getComputedStyle(e); return s.display !== 'none' && s.visibility !== 'hidden' && e.offsetParent !== null; })
              .map(e => (e.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 100))
              .filter(Boolean).slice(0, 3);
            return JSON.stringify({pct, ov, body: body.slice(0, 350)});
        """) or "{}"
    except Exception as e:
        return f'{{"hint_err": "{str(e)[:80]}"}}'


def _find_profile_url(driver, meta):
    """Best-effort public profile URL for the logged-in TikTok account."""
    import re
    try:
        hrefs = driver.execute_script(
            "return [...document.querySelectorAll('a[href]')].map(a=>a.href)"
        ) or []
        for h in hrefs:
            m = re.search(r"https?://(?:www\.)?tiktok\.com/@([\w.-]+)", h)
            if m and "/video/" not in h and "/photo/" not in h and "tiktokstudio" not in h:
                return f"https://www.tiktok.com/@{m.group(1)}"
    except Exception:
        pass
    # Avatar links are often JS-rendered; scan raw HTML for any /@handle.
    try:
        html = driver.execute_script("return document.documentElement.innerHTML") or ""
        for m in re.finditer(r"/@([\w.-]{2,24})", html):
            handle = m.group(1)
            ctx = html[max(0, m.start() - 100):m.start()]
            if handle.lower() == "tiktokstudio" or "/video/" in ctx or "/photo/" in ctx:
                continue
            return f"https://www.tiktok.com/@{handle}"
    except Exception:
        pass
    uname = str((meta or {}).get("username") or "").strip().lstrip("@")
    if re.fullmatch(r"[\w.]{2,24}", uname):
        return f"https://www.tiktok.com/@{uname}"
    return None


def upload_tiktok(video_path, description, account_id=None):
    """Upload video to TikTok using the persistent profile session
    (one-time manual login) or worker cookies as bootstrap."""
    cookies = _get_cookies("tiktok", account_id=account_id)
    if not cookies:
        print("[tiktok] no cookies in worker; relying on persistent profile session")

    meta = _account_meta(account_id)
    profile = _country_profile(meta.get("country"))
    if meta.get("proxy"):
        print(f"[tiktok] account {account_id} proxy: {meta['proxy']} "
              f"(country: {meta.get('country') or '-'})")

    print(f"[tiktok] video: {video_path} ({os.path.getsize(video_path)//1024} KB)")
    profile_dir = _browser_profile_dir("tiktok", account_id)
    driver = _make_driver(
        headless=True, proxy=meta.get("proxy"), country=meta.get("country"),
        profile_dir=profile_dir,
    )
    try:
        # Open TikTok on the persistent profile — if it's already logged in,
        # reuse that session (same 'device' every run = no fresh-profile signal).
        driver.get("https://www.tiktok.com/")
        _spoof(driver, languages=(profile or {}).get("languages"))
        has_session = _tiktok_logged_in(driver)
        if has_session:
            print("[tiktok] persistent profile session OK — skipping cookie inject")
        elif cookies:
            loaded = _load_cookies_for_driver(driver, cookies)
            if loaded == 0:
                print("[tiktok] zero cookies loaded; aborting")
                return _up(False, error="zero cookies loaded", account_id=account_id)
            driver.refresh()
            _sleep(3, 5)
            has_session = _tiktok_logged_in(driver)
            print(f"[tiktok] session after cookie inject: {has_session}")
        if not has_session:
            print("[tiktok] WARNING: no session cookie — may not be logged in "
                  "(run: python upload_social.py --login-tiktok)")

        # Organic For You feed browse first — uploading straight from a cold
        # open looks robotic; a short human-like scroll warms the session.
        _tiktok_warmup(driver, languages=(profile or {}).get("languages"))

        # Baseline: newest profile video BEFORE posting, so after posting we can
        # prove a NEW video appeared (never report a stale/pinned URL).
        # Detect the handle from the STUDIO page (avatar link) — the homepage
        # often has none, and stored usernames can be display names ("queen zoya").
        import re as _re
        studio_url = "https://www.tiktok.com/tiktokstudio/upload?from=creator_center"
        driver.get(studio_url)
        _spoof(driver, languages=(profile or {}).get("languages"))
        _sleep(3, 5)

        profile_url = _find_profile_url(driver, meta)
        baseline_id = None
        if profile_url:
            u0 = _harvest_profile_url(
                driver, r"/@[\w.-]+/(?:video|photo)/(\d+)", profile_url, timeout=25
            )
            m0 = _re.search(r"/(?:video|photo)/(\d+)", u0 or "")
            baseline_id = m0.group(1) if m0 else None
            print(f"[tiktok] profile: {profile_url} baseline newest id: {baseline_id or 'none'}")
            # Harvest navigated away — back to the upload page.
            driver.get(studio_url)
            _spoof(driver, languages=(profile or {}).get("languages"))
            _sleep(3, 5)
        else:
            print("[tiktok] profile url unknown; post-verify will rely on success dialog")
        print(f"[tiktok] page@studio: {_page_hint(driver)}")

        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        # Try iframe first, then direct
        try:
            iframe = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, 'iframe[data-tt="Upload_index_iframe"]')
                )
            )
            driver.switch_to.frame(iframe)
            print("[tiktok] switched to upload iframe")
        except Exception:
            print("[tiktok] no iframe found; trying direct")

        file_input = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located(
                (By.XPATH, "//input[@type='file']")
            )
        )
        file_input.send_keys(os.path.abspath(video_path))
        print("[tiktok] file input sent")
        _sleep(5, 8)
        print(f"[tiktok] page@uploaded-file: {_page_hint(driver)}")

        # Dismiss "Turn on automatic content checks?" EARLY — it blocks every
        # later click, and blindly ESC-ing this dialog can reset the panel.
        for xp in [
            "//button[.//div[text()='Cancel']]",
            "//button[contains(text(),'Cancel')]",
            "//button[.//div[text()='Not now']]",
            "//button[contains(text(),'Not now')]",
            "//div[contains(@class,'TUXModal')]//button[contains(@class,'close')]",
            "//button[.//div[text()='Turn on']]",
        ]:
            try:
                visible = [b for b in driver.find_elements(By.XPATH, xp) if b.is_displayed()]
                if visible:
                    driver.execute_script("arguments[0].click();", visible[0])
                    print(f"[tiktok] content-check dialog handled EARLY via {xp[:60]}")
                    _sleep(1, 2)
                    break
            except Exception:
                pass

        driver.switch_to.default_content()
        _sleep(2, 4)

        # Dismiss any modal/popup overlays that block clicks
        for attempt in range(3):
            try:
                # Try pressing ESC to close modals
                from selenium.webdriver.common.keys import Keys
                from selenium.webdriver.common.action_chains import ActionChains
                ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                _sleep(1, 2)
                # Try clicking close buttons on modals
                for sel in [
                    "//div[contains(@class,'TUXModal')]//button[contains(@class,'close')]",
                    "//div[contains(@class,'modal')]//button[@aria-label='Close']",
                    "//div[contains(@class,'Modal')]//*[name()='svg' and contains(@aria-label,'close')]",
                    "//button[@aria-label='Close']",
                ]:
                    try:
                        close_btn = driver.find_element(
                            __import__('selenium.webdriver.common.by', fromlist=['By']).By.XPATH, sel)
                        if close_btn.is_displayed():
                            close_btn.click()
                            print(f"[tiktok] dismissed modal via {sel[:40]}")
                            _sleep(1, 2)
                    except Exception:
                        pass
                # Check if overlay is gone
                overlays = driver.find_elements(
                    __import__('selenium.webdriver.common.by', fromlist=['By']).By.CSS_SELECTOR,
                    ".TUXModal-overlay, .modal-overlay, [class*='Modal-overlay']"
                )
                visible_overlays = [o for o in overlays if o.is_displayed()]
                if not visible_overlays:
                    print(f"[tiktok] overlays cleared after {attempt+1} attempts")
                    break
                print(f"[tiktok] {len(visible_overlays)} overlay(s) still visible; retrying...")
            except Exception as e:
                print(f"[tiktok] modal dismiss attempt {attempt+1} failed: {e}")
            _sleep(1, 2)

        # Fill caption
        try:
            from selenium.webdriver.common.by import By
            editable = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "div[contenteditable='true']")
                )
            )
            # Scroll into view and click via JS to avoid overlay interception
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", editable)
            _sleep(0.5, 1)
            driver.execute_script("arguments[0].click();", editable)
            _sleep(0.5, 1)
            # Select-all via JS Range (Ctrl+A left "final_edit" remnants).
            driver.execute_script("""
                const el = arguments[0];
                const r = document.createRange();
                r.selectNodeContents(el);
                const s = window.getSelection();
                s.removeAllRanges();
                s.addRange(r);
            """, editable)
            _sleep(0.3, 0.5)
            from selenium.webdriver.common.keys import Keys
            from selenium.webdriver.common.action_chains import ActionChains
            actions = ActionChains(driver)
            actions.send_keys(Keys.DELETE)
            actions.send_keys(description[:2200])
            actions.perform()
            print("[tiktok] caption filled via JS click")
        except Exception as e:
            print(f"[tiktok] caption failed: {e}")
        print(f"[tiktok] page@caption: {_page_hint(driver)}")

        # Studio sometimes resets to "Something went wrong" mid-flow — hit
        # Retry once and rebuild the form (re-send file + caption).
        try:
            _body_txt = driver.execute_script("return document.body.innerText") or ""
        except Exception:
            _body_txt = ""
        if "Something went wrong" in _body_txt:
            print("[tiktok] studio error page detected — recovering via Retry")
            try:
                rb = [b for b in driver.find_elements(By.XPATH, "//button[contains(text(),'Retry')]") if b.is_displayed()]
                if rb:
                    driver.execute_script("arguments[0].click();", rb[0])
                    _sleep(5, 8)
                fi = driver.find_elements(By.XPATH, "//input[@type='file']")
                if fi:
                    fi[0].send_keys(os.path.abspath(video_path))
                    print("[tiktok] re-sent file after retry")
                    _sleep(5, 8)
                for xp2 in ["//button[.//div[text()='Cancel']]", "//button[contains(text(),'Not now')]"]:
                    vis = [b for b in driver.find_elements(By.XPATH, xp2) if b.is_displayed()]
                    if vis:
                        driver.execute_script("arguments[0].click();", vis[0])
                        _sleep(1, 2)
                        break
                ed2 = [e for e in driver.find_elements(By.CSS_SELECTOR, "div[contenteditable='true']") if e.is_displayed()]
                if ed2:
                    driver.execute_script("arguments[0].click();", ed2[0])
                    _sleep(0.5, 1)
                    driver.execute_script(
                        "const el=arguments[0]; el.focus(); el.textContent=''; "
                        "el.dispatchEvent(new Event('input',{bubbles:true}));", ed2[0])
                    _sleep(0.5, 1)
                    from selenium.webdriver.common.action_chains import ActionChains as _AC2
                    _AC2(driver).send_keys(description[:2200]).perform()
                    print("[tiktok] caption re-filled after retry")
            except Exception as e:
                print(f"[tiktok] studio recovery failed: {e}")

        _sleep(5, 10)

        # Wait for upload complete — poll any indicator. TikTok shows
        # "Uploaded(53.67MB)" (not bare "Uploaded"), so use contains().
        uploaded = False
        _up_xpaths = [
            "//*[contains(text(),'Uploaded')]",
            "//*[contains(text(),'uploaded')]",
            "//*[contains(text(),'Upload complete')]",
            "//*[contains(text(),'upload complete')]",
        ]
        try:
            WebDriverWait(driver, 120).until(
                lambda d: any(d.find_elements(By.XPATH, x) for x in _up_xpaths)
            )
            uploaded = True
            print("[tiktok] upload complete indicator found")
        except Exception:
            print("[tiktok] upload wait timeout (120s, may still be processing)")
        print(f"[tiktok] page@post-attempt: {_page_hint(driver)}")

        # TikTok pops "Turn on automatic content checks?" after upload — it
        # blocks the Post button (click interception) so the post never lands.
        # Decline it (Cancel / Not now); accept only as last resort.
        for xp in [
            "//button[.//div[text()='Cancel']]",
            "//button[contains(text(),'Cancel')]",
            "//button[.//div[text()='Not now']]",
            "//button[contains(text(),'Not now')]",
            "//div[contains(@class,'TUXModal')]//button[contains(@class,'close')]",
            "//button[.//div[text()='Turn on']]",
        ]:
            try:
                visible = [b for b in driver.find_elements(By.XPATH, xp) if b.is_displayed()]
                if visible:
                    driver.execute_script("arguments[0].click();", visible[0])
                    print(f"[tiktok] content-check dialog handled via {xp[:60]}")
                    _sleep(1, 2)
                    break
            except Exception:
                pass

        # Hide leftover overlays/guides that can intercept the Post click.
        try:
            driver.execute_script("""
                document.querySelectorAll(
                  '.TUXModal-overlay,.modal-overlay,[class*="Modal-overlay"],[class*="Guide"]'
                ).forEach(e => { e.style.display = 'none'; });
            """)
        except Exception:
            pass

        # Click Post — trusted click first (JS click silently does nothing on
        # this button); treat as posted ONLY on real success signals
        # ("View post" dialog / navigation off the upload page).
        # Capture publish API calls so failures show their real server error.
        try:
            driver.execute_script("""
                window.__nova_net = [];
                const of = window.fetch;
                window.fetch = async (...a) => {
                  const r = await of(...a);
                  try {
                    const url = typeof a[0] === 'string' ? a[0] : (a[0] && a[0].url) || '';
                    if (/upload|publish|video|post|item/i.test(url)) {
                      const t = await r.clone().text().catch(() => '');
                      window.__nova_net.push({url: url.slice(0, 140), status: r.status, body: t.slice(0, 250)});
                    }
                  } catch (e) {}
                  return r;
                };
                const oxs = XMLHttpRequest.prototype.send;
                XMLHttpRequest.prototype.send = function (...a) {
                  this.addEventListener('loadend', () => {
                    try {
                      const u = this.responseURL || '';
                      if (/upload|publish|video|post|item/i.test(u)) {
                        window.__nova_net.push({url: u.slice(0, 140), status: this.status, body: (this.responseText || '').slice(0, 250)});
                      }
                    } catch (e) {}
                  });
                  return oxs.apply(this, a);
                };
            """)
        except Exception:
            pass

        post_selectors = [
            "//button[@data-e2e='post_video_button']",
            "//button[contains(@class,'TUXButton') and .//div[text()='Post']]",
            "//button[.//div[text()='Post']]",
            "//button[contains(text(),'Post')]",
        ]
        posted = False
        pub_url = None
        rejected_msg = ""
        for sel in post_selectors:
            try:
                post_btn = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, sel))
                )
                if not post_btn.is_enabled():
                    # Upload still in progress — wait for the button to enable.
                    print("[tiktok] Post disabled — waiting up to 240s for upload to finish")
                    WebDriverWait(driver, 240).until(EC.element_to_be_clickable((By.XPATH, sel)))
                    post_btn = driver.find_element(By.XPATH, sel)
                try:
                    print("[tiktok] post btn html:",
                          driver.execute_script("return arguments[0].outerHTML.slice(0,180);", post_btn))
                except Exception:
                    pass
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", post_btn)
                _sleep(0.5, 1)
                try:
                    post_btn.click()          # trusted events (isTrusted=true)
                except Exception:
                    driver.execute_script("arguments[0].click();", post_btn)
                # Wait up to ~60s for a REAL success signal; catch brief toasts.
                seen_toasts = set()
                for _ in range(120):
                    _sleep(0.5, 0.5)
                    try:
                        _toasts = driver.execute_script("""
                            return [...document.querySelectorAll(
                              '[class*="Toast"],[role="alert"],[class*="Snackbar"],[class*="notification" i]'
                            )].map(e => (e.innerText || '').replace(/\\s+/g, ' ').trim()).filter(Boolean);
                        """) or []
                        for _t2 in _toasts:
                            if _t2 not in seen_toasts:
                                seen_toasts.add(_t2)
                                print(f"[tiktok] toast: {_t2[:160]}")
                    except Exception:
                        pass
                    try:
                        _body = driver.execute_script("return document.body.innerText") or ""
                    except Exception:
                        _body = ""
                    _url = driver.current_url or ""
                    _low = _body.lower()
                    if ("view post" in _low or "video has been" in _low
                            or ("/upload" not in _url and "tiktokstudio" not in _url)):
                        pub_url = _harvest_url_js(driver, r"/@[\w.-]+/(?:video|photo)/\d+")
                        print(f"[tiktok] posted OK (selector: {sel[:50]}) url={pub_url or '-'}")
                        print(f"[tiktok] page@posted: {_page_hint(driver)}")
                        posted = True
                        break
                if posted:
                    break
                try:
                    _net = driver.execute_script("return JSON.stringify(window.__nova_net || [])") or "[]"
                    print(f"[tiktok] net after click: {_net[:900]}")
                    import json as _json
                    for _item in _json.loads(_net or "[]"):
                        _m = _re.search(r'"status_msg":\s*"([^"]+)"', _item.get("body") or "")
                        if _m:
                            rejected_msg = _m.group(1)
                            print("[tiktok] server-side rejection captured — stopping selector retries")
                            break
                except Exception:
                    pass
                if rejected_msg:
                    break
                print(f"[tiktok] click had no effect (still on upload page): {sel[:50]}")
            except Exception as e:
                print(f"[tiktok] post selector failed: {sel[:40]}: {e}")
                continue
        if not posted:
            if rejected_msg:
                print(f"[tiktok] rejected by TikTok API: {rejected_msg}")
                return _up(False, error=f"rejected by TikTok: {rejected_msg}"[:300],
                           account_id=account_id)
            print("[tiktok] Post never produced a success signal")
            return _up(False, error="post click did not publish (no success dialog)",
                       account_id=account_id)

        # Verify the NEW video actually landed — success dialog first, then a
        # profile poll for a video id NEWER than the baseline. A stale/pinned
        # URL is never reported; unverified posts return an honest failure.
        for _ in range(8):
            pub_url = _harvest_url_js(driver, r"/@[\w.-]+/(?:video|photo)/\d+")
            if pub_url:
                m = _re.search(r"/(?:video|photo)/(\d+)", pub_url)
                vid_found = m.group(1) if m else None
                if not baseline_id:
                    print(f"[tiktok] published URL (dialog, no baseline): {pub_url}")
                    return _up(True, url=pub_url, account_id=account_id)
                if vid_found and vid_found != baseline_id:
                    print(f"[tiktok] verified via success dialog: {pub_url}")
                    return _up(True, url=pub_url, account_id=account_id)
            _sleep(3, 5)
        if profile_url:
            for attempt in range(6):
                u = _harvest_profile_url(
                    driver, r"/@[\w.-]+/(?:video|photo)/(\d+)", profile_url, timeout=25
                )
                m = _re.search(r"/(?:video|photo)/(\d+)", u or "")
                if m and m.group(1) != baseline_id:
                    print(f"[tiktok] verified via profile (new id {m.group(1)}): {u}")
                    return _up(True, url=u, account_id=account_id)
                print(f"[tiktok] profile check {attempt + 1}/6: no new video yet")
        print("[tiktok] posted but NEW video NOT verified — honest failure (stale URL suppressed)")
        return _up(False, error="posted but new video not verified on profile",
                   account_id=account_id)
    except Exception as e:
        print(f"[tiktok] error: {e}")
        import traceback
        traceback.print_exc()
        return _up(False, error=str(e), account_id=account_id)
    finally:
        _quit(driver)


# ─────────────────────────── INSTAGRAM ───────────────────────────

def upload_instagram(video_path, description, account_id=None):
    """Upload reel to Instagram using cookies from worker."""
    cookies = _get_cookies("instagram", account_id=account_id)
    if not cookies:
        print("[instagram] no cookies in worker; skipping")
        return _up(False, error="no cookies", account_id=account_id)

    meta = _account_meta(account_id)
    print(f"[instagram] video: {video_path} ({os.path.getsize(video_path)//1024} KB)")
    driver = _make_driver(headless=True)
    try:
        driver.get("https://www.instagram.com/")
        _spoof(driver)
        loaded = _load_cookies_for_driver(driver, cookies)
        if loaded == 0:
            print("[instagram] zero cookies loaded; aborting")
            return _up(False, error="zero cookies loaded", account_id=account_id)
        driver.refresh()
        _sleep(3, 5)

        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        # Dismiss notification popup
        try:
            WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[contains(text(),'Not Now')]")
                )
            ).click()
        except Exception:
            pass

        # Click Create
        WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.XPATH, "//span[text()='Create']"))
        ).click()
        _sleep(1, 2)

        # Upload file
        file_input = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, '//input[@type="file"]'))
        )
        file_input.send_keys(video_path)
        _sleep(3, 5)

        # Click OK if present
        try:
            WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[text()='OK']"))
            ).click()
        except Exception:
            pass

        _sleep(2, 3)

        # Select portrait crop
        try:
            WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//*[name()='svg' and @aria-label='Select crop']",
                    )
                )
            ).click()
            _sleep(1, 2)
            WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//*[name()='svg' and @aria-label='Crop portrait icon']",
                    )
                )
            ).click()
        except Exception:
            pass

        _sleep(1, 2)

        # Next -> Next
        for _ in range(2):
            WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//*[name()='div' and text()='Next']")
                )
            ).click()
            _sleep(1, 2)

        # Fill caption
        try:
            caption_box = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//*[name()='div' and @aria-label='Write a caption...']",
                    )
                )
            )
            caption_box.send_keys(description[:2200])
        except Exception as e:
            print(f"[instagram] caption failed: {e}")

        _sleep(1, 2)

        # Share
        WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//*[name()='div' and text()='Share']")
            )
        ).click()

        # Wait for success
        try:
            WebDriverWait(driver, 120).until(
                lambda d: d.execute_script(
                    "return [...document.querySelectorAll('span')]"
                    ".some(s=>s.innerText.includes('Your reel has been shared'))"
                )
            )
            print("[instagram] reel shared OK")
            pub_url = _harvest_url_js(driver, r"/(reel|p)/[\w-]+")
            if not pub_url and meta.get("username"):
                uname = str(meta["username"]).lstrip("@")
                pub_url = _harvest_profile_url(
                    driver, r"/(reel|p)/[\w-]+",
                    f"https://www.instagram.com/{uname}/",
                )
            print(f"[instagram] published URL: {pub_url or '(not found)'}")
            return _up(True, url=pub_url, account_id=account_id)
        except Exception:
            print("[instagram] success message timeout (may still succeed)")
            pub_url = _harvest_url_js(driver, r"/(reel|p)/[\w-]+")
            if not pub_url and meta.get("username"):
                uname = str(meta["username"]).lstrip("@")
                pub_url = _harvest_profile_url(
                    driver, r"/(reel|p)/[\w-]+",
                    f"https://www.instagram.com/{uname}/",
                    timeout=20,
                )
            return _up(True, url=pub_url, account_id=account_id)
    except Exception as e:
        print(f"[instagram] error: {e}")
        return _up(False, error=str(e), account_id=account_id)
    finally:
        _quit(driver)




# ─────────────────────────── MAIN ───────────────────────────

def upload_all(video_path, title, description):
    """Upload to selected platforms (from task.dest_platforms). Returns results.
    dest_platforms format: "youtube,tiktok:3,instagram:5,facebook"
    where ":3" is optional account ID."""
    enabled = _get_enabled_platforms()
    results = {}

    if not enabled:
        print("[social] no platforms selected for this source")
        return results

    caption = f"{title}\n\n{description}".strip()[:2200]
    try:
        import upload_fb
        fb_creds = upload_fb.creds_available()
    except Exception:
        fb_creds = False
    ig_selected = False
    fb_selected = any(p == "facebook" for p, _ in enabled)

    for plat, acct_id in enabled:
        if plat == "tiktok":
            print(f"[social] uploading to TikTok{f' (account {acct_id})' if acct_id else ''}...")
            results["tiktok"] = upload_tiktok(video_path, caption, account_id=acct_id)
        elif plat == "instagram":
            # Graph API path — no cookies/browser (needs IG linked to Page).
            ig_selected = True
            print("[social] uploading to Instagram via Graph API...")
            try:
                import upload_instagram_api
                results["instagram"] = upload_instagram_api.upload(
                    video_path, caption=caption)
            except Exception as e:
                print(f"[social] instagram graph upload failed: {e}",
                      file=sys.stderr)
                results["instagram"] = {"success": False, "error": str(e)}
        elif plat == "facebook":
            if fb_creds:
                # Graph API path — no browser/cookies, works from any runner IP.
                sel = _fb_page_sel(acct_id)
                label = f" (page {sel})" if sel else ""
                print(f"[social] uploading to Facebook via Graph API{label}...")
                try:
                    import upload_fb
                    results["facebook"] = upload_fb.upload(
                        video_path, description=caption, page=sel)
                except Exception as e:
                    print(f"[social] facebook graph upload failed: {e}",
                          file=sys.stderr)
                    results["facebook"] = {"success": False, "error": str(e)}
            else:
                msg = ("no Facebook API creds — set FB_PAGES_JSON "
                       "(or FB_PAGE_TOKEN/FB_PAGE_ID)")
                print(f"[social] {msg}", file=sys.stderr)
                results["facebook"] = {"success": False, "error": msg}

    # Flow: IG selected -> ALSO mirror to the Facebook Page (Graph API),
    # unless facebook was already an explicit destination above.
    if ig_selected and not fb_selected and fb_creds:
        print("[social] instagram selected -> also uploading to "
              "Facebook Page (Graph API)...")
        try:
            import upload_fb
            results["facebook"] = upload_fb.upload(video_path,
                                                   description=caption)
        except Exception as e:
            print(f"[social] facebook mirror failed: {e}", file=sys.stderr)
            results["facebook"] = {"success": False, "error": str(e)}

    return results


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--login-tiktok":
        acct = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None
        sys.exit(0 if login_tiktok(acct) else 1)
    if len(sys.argv) < 3:
        print("Usage: python upload_social.py <video> <title> [description]")
        print("       python upload_social.py --login-tiktok [account_id]")
        sys.exit(1)
    v = sys.argv[1]
    t = sys.argv[2]
    d = sys.argv[3] if len(sys.argv) > 3 else ""
    r = upload_all(v, t, d)
    print(json.dumps(r))
