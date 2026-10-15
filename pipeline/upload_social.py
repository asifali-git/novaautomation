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


def _cookies_to_file(cookies, suffix):
    """Write cookies to a temp file. Handles dict/list/str/base64-pickle."""
    if isinstance(cookies, dict) and "__base64" in cookies:
        # Base64-encoded pickle from frontend
        data = base64.b64decode(cookies["__base64"])
        fd, path = tempfile.mkstemp(suffix=".pkl")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return path
    if isinstance(cookies, (dict, list)):
        fd, path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "w") as f:
            json.dump(cookies, f)
        return path
    if isinstance(cookies, str):
        fd, path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "w") as f:
            f.write(cookies)
        return path
    return None


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

    cookies_file = _cookies_to_file(cookies, ".pkl")
    if not cookies_file:
        print("[tiktok] invalid cookies format")
        return False

    driver = _make_driver(headless=True)
    try:
        driver.get("https://www.tiktok.com/login")
        _spoof(driver)
        with open(cookies_file, "rb") as f:
            loaded = pickle.load(f)
        if isinstance(loaded, list):
            for c in loaded:
                try:
                    driver.add_cookie(c)
                except Exception:
                    pass
        driver.refresh()
        _sleep(2, 4)

        driver.get(
            "https://www.tiktok.com/tiktokstudio/upload?from=creator_center"
        )
        _spoof(driver)
        _sleep(2, 4)

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
        except Exception:
            pass

        file_input = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located(
                (By.XPATH, "//input[@type='file']")
            )
        )
        file_input.send_keys(video_path)
        _sleep(3, 6)

        driver.switch_to.default_content()
        _sleep(2, 4)

        # Fill caption
        try:
            editable = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "div[contenteditable='true']")
                )
            )
            editable.click()
            from selenium.webdriver.common.keys import Keys
            from selenium.webdriver.common.action_chains import ActionChains
            actions = ActionChains(driver)
            actions.move_to_element(editable).click().perform()
            actions.key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL)
            actions.send_keys(Keys.DELETE)
            actions.send_keys(description[:2200])
            actions.perform()
        except Exception as e:
            print(f"[tiktok] caption failed: {e}")

        _sleep(5, 10)

        # Wait for upload complete
        try:
            WebDriverWait(driver, 120).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//span[text()='Uploaded']")
                )
            )
        except Exception:
            print("[tiktok] upload wait timeout (may still succeed)")

        # Click Post
        try:
            post_btn = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//button[contains(@class,'TUXButton') "
                        "and .//div[text()='Post']]",
                    )
                )
            )
            post_btn.click()
            _sleep(5, 10)
            print("[tiktok] posted OK")
            return True
        except Exception as e:
            print(f"[tiktok] post click failed: {e}")
            return False
    except Exception as e:
        print(f"[tiktok] error: {e}")
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

    cookies_file = _cookies_to_file(cookies, ".pkl")
    if not cookies_file:
        print("[instagram] invalid cookies format")
        return False

    driver = _make_driver(headless=True)
    try:
        driver.get("https://www.instagram.com/")
        _spoof(driver)
        with open(cookies_file, "rb") as f:
            loaded = pickle.load(f)
        if isinstance(loaded, list):
            for c in loaded:
                try:
                    driver.add_cookie(c)
                except Exception:
                    pass
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

    cookies_file = _cookies_to_file(cookies, ".json")
    if not cookies_file:
        print("[facebook] invalid cookies format")
        return False

    # Handle both JSON and Netscape formats
    if isinstance(cookies, str):
        # Raw string (Netscape) — write as .txt
        import tempfile as _tf
        fd, cookies_file = _tf.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write(cookies)

    page_url = os.getenv("FB_PAGE_URL", "")
    driver = _make_driver(headless=True)
    try:
        driver.get("https://www.facebook.com/")
        _spoof(driver)

        # Parse cookies (JSON or Netscape)
        cookie_list = _parse_cookies_any_format(cookies_file)
        print(f"[facebook] loaded {len(cookie_list)} cookies")

        for c in cookie_list:
            if not isinstance(c, dict) or "name" not in c:
                continue
            ck = {
                "name": c.get("name", ""),
                "value": c.get("value", ""),
                "domain": c.get("domain", ".facebook.com"),
                "path": c.get("path", "/"),
            }
            try:
                driver.add_cookie(ck)
            except Exception:
                pass

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
