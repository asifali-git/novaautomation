"""Social media upload via browser cookies (no API keys).

Uploads the final edited video to TikTok, Instagram, and Facebook
using Selenium + undetected-chromedriver with exported session cookies.

Env:
  UPLOAD_TIKTOK=true|false     enable TikTok upload (default false)
  UPLOAD_INSTAGRAM=true|false  enable Instagram upload (default false)
  UPLOAD_FACEBOOK=true|false   enable Facebook upload (default false)

  TIKTOK_COOKIES_B64      base64-encoded pickle cookies file
  INSTAGRAM_COOKIES_B64   base64-encoded pickle cookies file
  FACEBOOK_COOKIES_B64    base64-encoded JSON cookies file (Puppeteer format)

  FB_PAGE_URL             Facebook page URL to post on (optional)
"""
import os
import sys
import json
import time
import base64
import pickle
import random
import tempfile

ENABLE_TIKTOK = os.getenv("UPLOAD_TIKTOK", "false").lower() in ("1", "true", "yes")
ENABLE_INSTAGRAM = os.getenv("UPLOAD_INSTAGRAM", "false").lower() in ("1", "true", "yes")
ENABLE_FACEBOOK = os.getenv("UPLOAD_FACEBOOK", "false").lower() in ("1", "true", "yes")


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

def upload_tiktok(video_path, description):
    """Upload video to TikTok using cookies.pkl."""
    cookies_file = _decode_cookies_b64("TIKTOK_COOKIES_B64")
    if not cookies_file:
        print("[tiktok] no cookies; skipping")
        return False

    driver = _make_driver(headless=True)
    try:
        driver.get("https://www.tiktok.com/login")
        _spoof(driver)
        with open(cookies_file, "rb") as f:
            cookies = pickle.load(f)
        for c in cookies:
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

def upload_instagram(video_path, description):
    """Upload reel to Instagram using cookies.pkl."""
    cookies_file = _decode_cookies_b64("INSTAGRAM_COOKIES_B64")
    if not cookies_file:
        print("[instagram] no cookies; skipping")
        return False

    driver = _make_driver(headless=True)
    try:
        driver.get("https://www.instagram.com/")
        _spoof(driver)
        with open(cookies_file, "rb") as f:
            cookies = pickle.load(f)
        for c in cookies:
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

def upload_facebook(video_path, description):
    """Upload reel to Facebook using cookies.json (Puppeteer format)."""
    cookies_file = _decode_cookies_b64("FACEBOOK_COOKIES_B64")
    if not cookies_file:
        print("[facebook] no cookies; skipping")
        return False

    page_url = os.getenv("FB_PAGE_URL", "")
    driver = _make_driver(headless=True)
    try:
        driver.get("https://www.facebook.com/")
        _spoof(driver)

        # Load Puppeteer-format cookies
        with open(cookies_file, "r") as f:
            cookies = json.load(f)
        for c in cookies:
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
    """Upload to all enabled platforms. Returns dict of results."""
    tags = ""
    results = {}

    # Build a unified caption: title + description + hashtags
    caption = f"{title}\n\n{description}".strip()[:2200]

    if ENABLE_TIKTOK:
        print("[social] uploading to TikTok...")
        results["tiktok"] = upload_tiktok(video_path, caption)

    if ENABLE_INSTAGRAM:
        print("[social] uploading to Instagram...")
        results["instagram"] = upload_instagram(video_path, caption)

    if ENABLE_FACEBOOK:
        print("[social] uploading to Facebook...")
        results["facebook"] = upload_facebook(video_path, caption)

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
