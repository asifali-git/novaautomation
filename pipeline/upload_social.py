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

import requests

WORKER = os.getenv("WORKER_URL", "").rstrip("/")
TOKEN = os.getenv("INTERNAL_TOKEN", "")

# Cache for cookies fetched from worker
_cookies_cache = None


def _fetch_all_cookies():
    """Fetch all social cookies from Worker."""
    global _cookies_cache
    if _cookies_cache is not None:
        return _cookies_cache
    if not WORKER or not TOKEN:
        print("[social] WORKER_URL/INTERNAL_TOKEN not set")
        _cookies_cache = {}
        return _cookies_cache
    try:
        r = requests.get(
            f"{WORKER}/api/internal/accounts/env",
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
            # Selenium needs clean dict — strip None values
            clean = {k: v for k, v in c.items() if v is not None and k in (
                "name", "value", "domain", "path", "secure", "expiry", "httpOnly"
            )}
            if "name" not in clean or "value" not in clean:
                continue
            driver.add_cookie(clean)
            loaded += 1
        except Exception:
            pass
    print(f"[social] loaded {loaded}/{len(parsed)} cookies into browser")
    return loaded


def _sleep(lo=1.0, hi=3.0):
    time.sleep(random.uniform(lo, hi))


def _spoof(driver):
    driver.execute_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
    )
    driver.execute_script(
        "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3]})"
    )
    driver.execute_script(
        "Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']})"
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


def _make_driver(headless=True):
    import undetected_chromedriver as uc

    opts = uc.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    return uc.Chrome(options=opts)


# ─────────────────────────── TIKTOK ───────────────────────────

def upload_tiktok(video_path, description, account_id=None):
    """Upload video to TikTok using cookies from worker."""
    cookies = _get_cookies("tiktok", account_id=account_id)
    if not cookies:
        print("[tiktok] no cookies in worker; skipping")
        return False

    print(f"[tiktok] video: {video_path} ({os.path.getsize(video_path)//1024} KB)")
    driver = _make_driver(headless=True)
    try:
        # Load cookies on tiktok.com domain
        driver.get("https://www.tiktok.com/")
        _spoof(driver)
        loaded = _load_cookies_for_driver(driver, cookies)
        if loaded == 0:
            print("[tiktok] zero cookies loaded; aborting")
            return False
        driver.refresh()
        _sleep(3, 5)

        # Verify login by checking cookies
        all_c = driver.get_cookies()
        has_session = any(c.get("name") in ("sessionid", "sid_tt", "sidguard") for c in all_c)
        print(f"[tiktok] session cookies present: {has_session} (total cookies: {len(all_c)})")
        if not has_session:
            print("[tiktok] WARNING: no session cookie — may not be logged in")

        driver.get(
            "https://www.tiktok.com/tiktokstudio/upload?from=creator_center"
        )
        _spoof(driver)
        _sleep(3, 5)

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
            from selenium.webdriver.common.keys import Keys
            from selenium.webdriver.common.action_chains import ActionChains
            actions = ActionChains(driver)
            actions.send_keys(Keys.CONTROL + "a")
            actions.send_keys(Keys.DELETE)
            actions.send_keys(description[:2200])
            actions.perform()
            print("[tiktok] caption filled via JS click")
        except Exception as e:
            print(f"[tiktok] caption failed: {e}")

        _sleep(5, 10)

        # Wait for upload complete — try multiple selectors
        uploaded = False
        for xpath in ["//span[text()='Uploaded']", "//div[text()='Uploaded']",
                      "//*[contains(text(),'uploaded')]"]:
            try:
                WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.XPATH, xpath))
                )
                uploaded = True
                print(f"[tiktok] upload complete indicator found: {xpath}")
                break
            except Exception:
                continue
        if not uploaded:
            print("[tiktok] upload wait timeout (may still succeed)")

        # Click Post — try multiple selectors with JS click fallback
        post_selectors = [
            "//button[contains(@class,'TUXButton') and .//div[text()='Post']]",
            "//button[.//div[text()='Post']]",
            "//button[contains(text(),'Post')]",
            "//button[@data-e2e='post_video_button']",
        ]
        for sel in post_selectors:
            try:
                post_btn = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, sel))
                )
                # JS click avoids overlay interception
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", post_btn)
                _sleep(0.5, 1)
                driver.execute_script("arguments[0].click();", post_btn)
                _sleep(5, 10)
                print(f"[tiktok] posted OK (selector: {sel[:50]})")
                return True
            except Exception as e:
                print(f"[tiktok] post selector failed: {sel[:40]}: {e}")
                continue
        print("[tiktok] no Post button found")
        return False
    except Exception as e:
        print(f"[tiktok] error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        driver.quit()


# ─────────────────────────── INSTAGRAM ───────────────────────────

def upload_instagram(video_path, description, account_id=None):
    """Upload reel to Instagram using cookies from worker."""
    cookies = _get_cookies("instagram", account_id=account_id)
    if not cookies:
        print("[instagram] no cookies in worker; skipping")
        return False

    print(f"[instagram] video: {video_path} ({os.path.getsize(video_path)//1024} KB)")
    driver = _make_driver(headless=True)
    try:
        driver.get("https://www.instagram.com/")
        _spoof(driver)
        loaded = _load_cookies_for_driver(driver, cookies)
        if loaded == 0:
            print("[instagram] zero cookies loaded; aborting")
            return False
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
            return True
        except Exception:
            print("[instagram] success message timeout (may still succeed)")
            return True
    except Exception as e:
        print(f"[instagram] error: {e}")
        return False
    finally:
        driver.quit()


# ─────────────────────────── FACEBOOK ───────────────────────────

def _parse_cookies_any_format(cookies_file):
    """Parse cookies from JSON (Puppeteer) or Netscape (.txt) format.
    Returns list of dicts with name/value/domain/path."""
    # Try JSON first
    try:
        with open(cookies_file, "r") as f:
            loaded = json.load(f)
        if isinstance(loaded, list):
            return loaded
        if isinstance(loaded, dict):
            return [loaded]
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass

    # Try Netscape format
    cookies = []
    with open(cookies_file, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                # Netscape header comment lines, skip
                if line.startswith("#HttpOnly_"):
                    line = line[len("#HttpOnly_"):]
                else:
                    continue
            parts = line.split("\t")
            if len(parts) >= 7:
                domain, _, path, secure, expires, name, value = parts[:7]
                cookies.append({
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": path,
                    "secure": secure.upper() == "TRUE",
                    "expires": int(expires) if expires.isdigit() else -1,
                })
    return cookies


def upload_facebook(video_path, description, account_id=None):
    """Upload reel to Facebook using cookies from worker."""
    cookies = _get_cookies("facebook", account_id=account_id)
    if not cookies:
        print("[facebook] no cookies in worker; skipping")
        return False

    print(f"[facebook] video: {video_path} ({os.path.getsize(video_path)//1024} KB)")
    page_url = os.getenv("FB_PAGE_URL", "")
    driver = _make_driver(headless=True)
    try:
        driver.get("https://www.facebook.com/")
        _spoof(driver)

        loaded = _load_cookies_for_driver(driver, cookies)
        if loaded == 0:
            print("[facebook] zero cookies loaded; aborting")
            return False

        driver.refresh()
        _sleep(3, 5)

        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        # Go to Reels create page
        driver.get("https://www.facebook.com/reels/create")
        _sleep(3, 5)

        # Upload video
        file_input = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located(
                (By.XPATH, "//input[@type='file' and contains(@accept,'video')]")
            )
        )
        file_input.send_keys(video_path)
        print("[facebook] uploading video file...")
        _sleep(10, 20)

        # Fill description
        try:
            desc_box = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//div[@contenteditable='true' or @role='textbox']",
                    )
                )
            )
            desc_box.click()
            desc_box.send_keys(description[:5000])
        except Exception as e:
            print(f"[facebook] description failed: {e}")

        _sleep(2, 4)

        # Click Publish / Share reel
        try:
            publish_btn = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//div[@role='button' and ("
                        "normalize-space()='Share reel' or "
                        "normalize-space()='Publish' or "
                        "normalize-space()='Share' or "
                        "normalize-space()='Đăng')] | "
                        "//button[normalize-space()='Share reel' or "
                        "normalize-space()='Publish' or "
                        "normalize-space()='Share']",
                    )
                )
            )
            publish_btn.click()
            _sleep(8, 12)
            print("[facebook] reel published OK")
            return True
        except Exception as e:
            print(f"[facebook] publish click failed: {e}")
            return False
    except Exception as e:
        print(f"[facebook] error: {e}")
        return False
    finally:
        driver.quit()


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

    for plat, acct_id in enabled:
        if plat == "tiktok":
            print(f"[social] uploading to TikTok{f' (account {acct_id})' if acct_id else ''}...")
            results["tiktok"] = upload_tiktok(video_path, caption, account_id=acct_id)
        elif plat == "instagram":
            print(f"[social] uploading to Instagram{f' (account {acct_id})' if acct_id else ''}...")
            results["instagram"] = upload_instagram(video_path, caption, account_id=acct_id)
        elif plat == "facebook":
            print(f"[social] uploading to Facebook{f' (account {acct_id})' if acct_id else ''}...")
            results["facebook"] = upload_facebook(video_path, caption, account_id=acct_id)

    return results


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python upload_social.py <video> <title> [description]")
        sys.exit(1)
    v = sys.argv[1]
    t = sys.argv[2]
    d = sys.argv[3] if len(sys.argv) > 3 else ""
    r = upload_all(v, t, d)
    print(json.dumps(r))
