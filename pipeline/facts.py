"""Dynamic Fact Vault — verified facts + angle rotation for voiceover.

Each subject has a JSON vault with 4 angle categories (A-D). Every video
picks 3 facts from the LEAST-used angle (hash tiebreak so even without
persisted state different videos spread across facts), marks them used, and
the plan prompt is instructed to fold exactly these facts into the three
narrated segments (hook / mid fact / end question) plus a fact-led title.
State is committed back by the process workflow after each run.
"""
import hashlib
import json
import os

FACTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "facts")

SUBJECT_KEYWORDS = {
    "coconut": ("coconut", "kelapa", "daab", "thambili", "buko", "coco",
                "copra", "todd", "nariyal"),
    "watermelon": ("watermelon", "semangka", "tarbuaz", "kharbooz", "tarbooz"),
}


def detect_subject(text):
    low = (text or "").lower()
    for name, kws in SUBJECT_KEYWORDS.items():
        if any(k in low for k in kws):
            return name
    return None


def _vault_files():
    if not os.path.isdir(FACTS_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(FACTS_DIR) if f.endswith(".json"))


def _load(name):
    with open(os.path.join(FACTS_DIR, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def _save(name, data):
    with open(os.path.join(FACTS_DIR, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def _h(text):
    return int(hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:12], 16)


def pick_facts(title_hint="", video_key="", n=3):
    """Returns (angle_label, [fact_text, ...]) — least-used angle, least-used
    facts inside it, hash tiebreak keyed by video so videos never collide."""
    files = _vault_files()
    if not files:
        return "", []
    subject = detect_subject(title_hint)
    name = subject if subject in files else files[0]
    data = _load(name)
    angles = {a: fs for a, fs in (data.get("angles") or {}).items() if fs}
    if not angles:
        return "", []
    seed = video_key or title_hint or name
    angle_key = sorted(
        angles.items(),
        key=lambda kv: (sum(int(f.get("used", 0)) for f in kv[1]),
                        _h(seed + kv[0])),
    )[0][0]
    picked = sorted(
        angles[angle_key],
        key=lambda f: (int(f.get("used", 0)), _h(seed + str(f.get("id", "")))),
    )[:n]
    if not picked:
        return "", []
    for f in picked:
        f["used"] = int(f.get("used", 0)) + 1
    try:
        _save(name, data)
    except Exception:
        pass
    names = data.get("angle_names") or {}
    label = f"{angle_key} - {names.get(angle_key, angle_key)}"
    return label, [str(f.get("text", "")).strip() for f in picked if f.get("text")]


def build_note(title_hint="", video_key=""):
    """Prompt block handed to the AI: use ONLY these verified facts."""
    try:
        angle, facts = pick_facts(title_hint=title_hint, video_key=video_key)
    except Exception as e:
        print(f"[facts] vault error ({e})")
        return ""
    if not facts:
        return ""
    lines = "\n".join(f"{i + 1}) {t}" for i, t in enumerate(facts))
    print(f"[facts] angle={angle} n={len(facts)}")
    return (
        f"VERIFIED FACTS FOR THIS VIDEO (angle: {angle}):\n{lines}\n"
        "USE ALL THREE — they REPLACE free fact generation: rewrite each into "
        "ONE natural 10-15-word narration line, same meaning, no new claims, "
        "no invented numbers. First narrated segment = hook (lead with fact 1 "
        "as a bold claim or question), middle narrated segment = fact 2 stated "
        "plainly, last narrated segment = wrap fact 3 into a comment-inviting "
        "question. Build the TITLE around the strongest of these three facts "
        "(e.g. 'Why Street Vendors Tap Before Slicing'). "
        "SAFETY: if a given fact is wrong for the subject actually visible in "
        "the frames (mismatched vault), drop that line and use your best true "
        "fact instead — never state a false fact."
    )
