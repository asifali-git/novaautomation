"""Instagram upload via the Instagram Graph API — no cookies/browser.

Requirements (one-time, done by the account owner):
  * IG account is Professional (Business or Creator)
  * IG is linked to the Facebook Page we hold the token for (FB_PAGE_ID)
Then the SAME FB_PAGE_TOKEN works — no separate Instagram token.

Constraint: IG cannot pull files off this runner — a video needs a PUBLIC
url, so the file is first pushed to a temporary host (uguu.se default;
env VIDEO_PUBLIC_HOST=litterbox to switch provider).

Flow: host file -> POST /{ig}/media (REELS, video_url) -> poll container
status -> POST /{ig}/media_publish. Unpublished containers can be deleted
(that is what the e2e test uses — published IG media cannot be deleted via
API).
"""
import os
import sys
import time

import requests

GRAPH = f"https://graph.facebook.com/{os.getenv('GRAPH_VERSION', 'v23.0')}"
CAPTION_MAX = 2200


def _creds():
    tok = (os.getenv("FB_PAGE_TOKEN") or "").strip()
    pid = (os.getenv("FB_PAGE_ID") or "").strip()
    if not tok or not pid:
        raise RuntimeError("FB_PAGE_TOKEN / FB_PAGE_ID not set")
    return tok, pid


def _ig_user_id(tok, pid):
    """Linked IG professional account id for the Page."""
    r = requests.get(f"{GRAPH}/{pid}",
                     params={"fields": "instagram_business_account",
                             "access_token": tok},
                     timeout=30)
    j = r.json() if r.content else {}
    ig = j.get("instagram_business_account") or {}
    if not ig.get("id"):
        err = j.get("error")
        msg = (err.get("message") if isinstance(err, dict) else None) or \
              "no instagram_business_account on page"
        raise RuntimeError(
            "Instagram not linked to this Facebook Page — link it first "
            "(Page Settings -> Linked Accounts -> Instagram; IG must be a "
            f"Professional account). graph said: {msg[:160]}")
    return str(ig["id"])


def _host(path):
    """Push the video to a temporary public host; return a DIRECT file url.

    Default uguu.se (anonymous, JSON API, 24h expiry, 128MB cap — fine for
    Shorts). VIDEO_PUBLIC_HOST=litterbox switches provider.
    """
    provider = (os.getenv("VIDEO_PUBLIC_HOST") or "uguu").lower()
    name = os.path.basename(path)
    ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    with open(path, "rb") as f:
        if provider == "litterbox":
            r = requests.post(
                "https://litterbox.catbox.moe/resources/internals/api.php",
                data={"reqtype": "fileupload", "time": "12h"},
                files={"fileToUpload": (name, f, "video/mp4")},
                headers=ua, timeout=600)
            url = (r.text or "").strip()
            if r.status_code != 200 or not url.startswith("http"):
                raise RuntimeError(
                    f"litterbox upload failed HTTP {r.status_code}")
            return url
        r = requests.post("https://uguu.se/upload.php",
                          files=[("files[]", (name, f, "video/mp4"))],
                          headers=ua, timeout=600)
        j = r.json() if r.content else {}
        fl = j.get("files") or []
        url = str(fl[0].get("url") or "") if fl else ""
        if r.status_code != 200 or not url.startswith("http"):
            raise RuntimeError(
                f"uguu upload failed HTTP {r.status_code} ({str(j)[:120]})")
        return url


def upload(video_path, caption="", media_type="REELS", poll_s=300):
    """Publish a video to the linked IG. Returns {success, id, url, kind}.
    Never echoes the token."""
    tok, pid = _creds()
    if not os.path.isfile(video_path):
        raise FileNotFoundError(video_path)
    ig = _ig_user_id(tok, pid)

    pub_url = _host(video_path)
    print(f"[ig] video hosted: {os.path.basename(video_path)}")

    cap = (caption or "").strip()[:CAPTION_MAX]
    r = requests.post(f"{GRAPH}/{ig}/media",
                      data={"media_type": media_type,
                            "video_url": pub_url,
                            "caption": cap,
                            "access_token": tok},
                      timeout=60)
    j = r.json() if r.content else {}
    cid = str(j.get("id") or "")
    if r.status_code != 200 or not cid:
        err = j.get("error")
        msg = (err.get("message") if isinstance(err, dict) else None) or str(j)[:200]
        raise RuntimeError(f"container create failed HTTP {r.status_code}: "
                           f"{str(msg)[:300]}")

    # Poll container until IG finishes transcoding (max poll_s seconds).
    deadline = time.time() + poll_s
    state = ""
    while time.time() < deadline:
        try:
            rr = requests.get(f"{GRAPH}/{cid}",
                              params={"fields": "status_code",
                                      "access_token": tok},
                              timeout=30)
            state = str((rr.json() or {}).get("status_code") or "")
        except Exception:
            state = ""
        if state == "FINISHED":
            break
        if state in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"ig container {state.lower()} (bad codec/size/"
                               f"url) — video not publishable")
        time.sleep(5)
    else:
        raise RuntimeError(f"ig container status timeout (last={state or '?'})")

    r2 = requests.post(f"{GRAPH}/{ig}/media_publish",
                       data={"creation_id": cid, "access_token": tok},
                       timeout=60)
    j2 = r2.json() if r2.content else {}
    mid = str(j2.get("id") or "")
    if r2.status_code != 200 or not mid:
        err = j2.get("error")
        msg = (err.get("message") if isinstance(err, dict) else None) or str(j2)[:200]
        raise RuntimeError(f"media_publish failed HTTP {r2.status_code}: "
                           f"{str(msg)[:300]}")

    permalink = ""
    try:
        rp = requests.get(f"{GRAPH}/{mid}",
                          params={"fields": "permalink", "access_token": tok},
                          timeout=30)
        permalink = str((rp.json() or {}).get("permalink") or "")
    except Exception:
        pass
    print(f"[ig] published: {permalink or mid}")
    return {"success": True, "id": mid, "url": permalink, "kind": media_type.lower()}


def delete_container(creation_id):
    """Delete an UNPUBLISHED container (e2e cleanup; published media has no
    delete API)."""
    tok, _pid = _creds()
    r = requests.delete(f"{GRAPH}/{creation_id}",
                        params={"access_token": tok}, timeout=60)
    ok = r.status_code == 200 and bool((r.json() or {}).get("success"))
    print(f"[ig] delete container {creation_id} -> ok={ok}")
    return ok


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--delete-container":
        sys.exit(0 if delete_container(args[1]) else 1)
    if not args:
        print("Usage: python upload_instagram_api.py <video> [caption]")
        sys.exit(2)
    out = upload(args[0], caption=args[1] if len(args) > 1 else "NOVA e2e")
    print(out)
