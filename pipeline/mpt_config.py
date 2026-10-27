"""Write MoneyPrinterTurbo's config.toml for NOVA's free documentary path.

Takes the upstream config.example.toml and only changes the stock-clip settings,
so every other default stays exactly as MoneyPrinterTurbo ships it.

Env:
  MPT_DIR            repo path (default ./mpt, else D:\\repos\\MoneyPrinterTurbo)
  PIXABAY_API_KEYS   comma separated keys (or PIXABAY_API_KEY)
  COVERR_API_KEYS    optional
  VIDEO_SOURCE       default "pixabay"

Usage:
  python pipeline/mpt_config.py            # write <MPT_DIR>/config.toml
  python pipeline/mpt_config.py --print    # just validate and show the result
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path

DEFAULT_WINDOWS_DIR = r"D:\repos\MoneyPrinterTurbo"


def mpt_dir() -> Path:
    env = os.getenv("MPT_DIR")
    if env:
        return Path(env)
    return Path(DEFAULT_WINDOWS_DIR) if os.name == "nt" else Path("mpt")


def keys_from_env(*names: str) -> list[str]:
    out: list[str] = []
    for name in names:
        for value in (os.getenv(name) or "").split(","):
            value = value.strip()
            if value and value not in out:
                out.append(value)
    return out


def build_config(example: str, source: str, pixabay: list[str], coverr: list[str]) -> str:
    text = example
    if 'video_source = "pexels"' in text:
        text = text.replace('video_source = "pexels"', f'video_source = "{source}"')
    if pixabay:
        text = text.replace("pixabay_api_keys = []", 'pixabay_api_keys = ["' + '", "'.join(pixabay) + '"]')
    if coverr:
        text = text.replace("coverr_api_keys = []", 'coverr_api_keys = ["' + '", "'.join(coverr) + '"]')
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description="write MoneyPrinterTurbo config.toml (Pixabay path)")
    parser.add_argument("--print", dest="show", action="store_true", help="validate only, do not write")
    args = parser.parse_args()

    root = mpt_dir()
    example_path = root / "config.example.toml"
    target = root / "config.toml"
    if not example_path.exists():
        print(f"[mpt_config] missing {example_path} - is MPT_DIR correct?", file=sys.stderr)
        return 2

    source = os.getenv("VIDEO_SOURCE") or "pixabay"
    pixabay = keys_from_env("PIXABAY_API_KEYS", "PIXABAY_API_KEY")
    coverr = keys_from_env("COVERR_API_KEYS", "COVERR_API_KEY")
    if source == "pixabay" and not pixabay:
        print("[mpt_config] PIXABAY_API_KEYS is empty - Pixabay search will fail", file=sys.stderr)

    text = build_config(example_path.read_text(encoding="utf-8"), source, pixabay, coverr)
    try:
        cfg = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        print(f"[mpt_config] generated TOML is invalid: {exc}", file=sys.stderr)
        return 1

    app = cfg.get("app", {})
    print(
        f"[mpt_config] source={app.get('video_source')} "
        f"pixabay_keys={len(app.get('pixabay_api_keys') or [])} "
        f"coverr_keys={len(app.get('coverr_api_keys') or [])}"
    )
    if args.show:
        return 0

    target.write_text(text, encoding="utf-8")
    print(f"[mpt_config] wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
