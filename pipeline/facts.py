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
import re

FACTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "facts")

_CTX_STOP = {"the", "a", "an", "of", "on", "in", "to", "and", "is", "it", "with",
             "for", "at", "by", "from", "being", "shows", "shown", "video", "clip",
             "person", "man", "woman", "hand", "hands", "close", "shot", "while",
             "that", "this", "are", "has", "his", "her", "they", "into", "over"}

SUBJECT_KEYWORDS = {
    "coconut": ("coconut", "kelapa", "daab", "thambili", "buko", "coco",
                "copra", "todd", "nariyal"),
    "watermelon": ("watermelon", "semangka", "tarbuaz", "kharbooz", "tarbooz"),
    "food": ("food", "fruit", "fruits", "fruit's", "vegetable", "vegetables",
             "veggie", "veggies", "mango", "banana", "apple", "pineapple",
             "papaya", "guava", "orange", "lemon", "lime", "grape", "grapes",
             "strawberry", "blueberry", "raspberry", "kiwi", "pomegranate",
             "cherry", "peach", "pear", "plum", "apricot", "avocado", "lychee",
             "jackfruit", "durian", "passion fruit", "melon", "cantaloupe",
             "tomato", "potato", "onion", "garlic", "ginger", "carrot",
             "cucumber", "pumpkin", "cabbage", "cauliflower", "chili", "chilli",
             "pepper", "eggplant", "aubergine", "broccoli", "spinach", "corn",
             "maize", "radish", "beetroot", "celery", "lettuce", "okra",
             "recipe", "recipes", "cooking", "cook", "kitchen", "fry", "frying",
             "fried", "curry", "bake", "baking", "bakery", "chef", "dough",
             "spice", "spices", "grill", "grilling", "steam", "steaming",
             "boil", "boiling", "roast", "roasting", "sauce", "noodle",
             "noodles", "rice", "bread", "cake", "soup", "salad", "sandwich",
             "street food", "breakfast", "dinner", "lunch", "snack", "dessert",
             "chocolate", "cheese", "butter", "oil", "sugar", "honey", "tea",
             "coffee", "juice", "smoothie"),
    "animals": ("animal", "animals", "pet", "pets", "dog", "dogs", "cat", "cats",
                "puppy", "puppies", "kitten", "kittens", "bird", "birds", "fish",
                "fishes", "shark", "whale", "dolphin", "tiger", "lion", "leopard",
                "cheetah", "elephant", "monkey", "gorilla", "snake", "python",
                "cobra", "rabbit", "hare", "hamster", "guinea pig", "horse",
                "pony", "cow", "calf", "buffalo", "pig", "goat", "sheep", "hen",
                "rooster", "chicken", "duck", "goose", "turkey", "eagle", "owl",
                "parrot", "peacock", "pigeon", "sparrow", "crow", "bear", "deer",
                "fox", "wolf", "hyena", "panda", "koala", "kangaroo", "zebra",
                "giraffe", "hippo", "rhino", "crocodile", "alligator", "lizard",
                "gecko", "turtle", "tortoise", "frog", "toad", "insect",
                "butterfly", "bee", "ant", "spider", "scorpion", "centipede",
                "mouse", "rat", "squirrel", "hedgehog", "bat", "otter", "seal",
                "penguin", "ostrich", "flamingo", "swan", "crab", "lobster",
                "octopus", "jellyfish", "seahorse", "squirrel", "raccoon"),
    "asmr": ("asmr", "oddly satisfying", "satisfying", "kinetic sand", "slime",
             "squishy", "squish", "scraping", "shaving", "carving", "chopping",
             "slicing", "cutting", "crushing", "pressing", "stacking", "pouring",
             "peeling", "restoration", "polishing", "cleaning", "scrubbing",
             "sanding", "unboxing", "fidget", "pop it", "bubble wrap", "soap",
             "gummy", "jelly", "wax", "resin", "paint mixing", "clay"),
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


def _resolve_name(subject, title_hint, files):
    """Resolve vault file: micro subject > keyword detect (subject then title)
    > exact file token. Returns file name or None (caller sends NO facts)."""
    fset = set(files)
    for text in (subject or "", title_hint or ""):
        if not text:
            continue
        s = detect_subject(text)
        if s and s in fset:
            return s
        for tok in str(text).lower().replace("-", " ").replace("/", " ").split():
            if tok in fset:
                return tok
    return None


def _ctx_tokens(text):
    return {t for t in re.findall(r"[a-z]{3,}", (text or "").lower())
            if t not in _CTX_STOP}


def _score(fact_text, ctx_tokens):
    if not ctx_tokens:
        return 0
    return len(_ctx_tokens(fact_text) & ctx_tokens)


def pick_facts(title_hint="", video_key="", n=3, subject="", context=""):
    """Returns (angle_label, [fact_text, ...]) — least-used angle, least-used
    facts inside it, hash tiebreak keyed by video so videos never collide.
    `subject`/`context` = micro-analysis results (priority); title keywords are
    backup. CONTEXT FILTER: facts are scored by token overlap with what the
    video actually shows — e.g. a fresh-coconut-water clip must NOT get dry
    copra / oil-lamp facts (feedback). Strong match => only fit facts are
    eligible; weak signal => full angle (never return nothing over scoring).
    No vault match → ("", []) — caller sends no facts (never a wrong vault)."""
    files = _vault_files()
    if not files:
        return "", []
    name = _resolve_name(subject, title_hint, files)
    if not name:
        return "", []
    data = _load(name)
    angles = {a: fs for a, fs in (data.get("angles") or {}).items() if fs}
    if not angles:
        return "", []
    seed = video_key or title_hint or name
    ctx = _ctx_tokens(str(subject or "") + " " + str(context or "") + " "
                      + str(title_hint or ""))
    pools = {}
    fit_keys = set()
    for akey, facts_list in angles.items():
        if ctx:
            scored = [(_score(str(f.get("text") or ""), ctx), f)
                      for f in facts_list]
            best = max((s for s, _ in scored), default=0)
            if best >= 2:
                fit = [f for s, f in scored if s >= max(2, best - 1)]
                if len(fit) >= n:
                    pools[akey] = fit
                    fit_keys.add(akey)
                    continue
        pools[akey] = facts_list
    candidates = {k: pools[k] for k in (fit_keys or pools.keys())}
    angle_key = sorted(
        candidates.items(),
        key=lambda kv: (sum(int(f.get("used", 0)) for f in kv[1]),
                        _h(seed + kv[0])),
    )[0][0]
    picked = sorted(
        candidates[angle_key],
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


def build_note(title_hint="", video_key="", subject="", context="", n=3):
    """Prompt block handed to the AI: use ONLY these verified facts."""
    try:
        angle, facts = pick_facts(title_hint=title_hint, video_key=video_key,
                                  subject=subject, context=context, n=max(1, int(n or 3)))
    except Exception as e:
        print(f"[facts] vault error ({e})")
        return ""
    if not facts:
        return ""
    lines = "\n".join(f"{i + 1}) {t}" for i, t in enumerate(facts))
    nf = len(facts)
    print(f"[facts] angle={angle} n={nf}")
    if nf == 1:
        mid = "the single line carries the whole fact"
    else:
        mid = (f"middle narrated segments = facts 2..{max(2, nf - 1)} "
               f"stated plainly and spread evenly across the video, ")
    return (
        f"VERIFIED FACTS FOR THIS VIDEO (angle: {angle}):\n{lines}\n"
        f"USE ALL {nf} — they REPLACE free fact generation: rewrite each into "
        f"ONE natural 10-15-word narration line, same meaning, no new claims, "
        f"no invented numbers. First narrated segment = hook (lead with fact 1 "
        f"as a bold claim or question), {mid}last narrated segment "
        f"= wrap fact {nf} into a comment-inviting "
        f"question that ENDS the video (voice runs to the final seconds, never "
        f"stops early leaving silence). Build the TITLE around the strongest of "
        f"these facts (e.g. 'Why Street Vendors Tap Before Slicing'). "
        "CONTEXT: the facts must FIT what the frames actually show (fresh "
        "coconut-water video gets water/tender-pulp facts, dry-copra videos "
        "get processing facts). SAFETY: if a given fact is wrong for the "
        "subject actually visible in the frames (mismatched vault), drop that "
        "line and use your best true fact instead — never state a false fact."
    )
