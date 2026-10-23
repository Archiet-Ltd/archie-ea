#!/usr/bin/env python
"""Every live module, role and use-case page carries a real screenshot, or the build fails.

Three times in one week a published, otherwise-finished public page went out with no
screenshot (the four role pages, the Diagrams & Composer module page) and a finished
page sat unpublished. Each was found by a person happening to look. This gate makes the
check mechanical.

"Live" is decided by the one canonical source the sitemap, /llms.txt and the IndexNow
command already share: `load_feed_pages()` (not held by `PublicPage.is_held`, not a
merged page). Held pages (`state` other than `on_main`, or listed in `HELD_PAGE_URLS`)
are therefore exempt until they are promoted, exactly as they are exempt from the
sitemap. For every live page whose `page_family` is `module`, `role` or
`function-per-segment` (the use-case family), `get_page_screenshot(page)` must return an
image, and the file behind it must be a real image: present, at least MIN_BYTES, decodable
and at least MIN_WIDTH x MIN_HEIGHT pixels. No second page-discovery or screenshot
mechanism is built here.

Known offenders are listed by URL in scripts/public_page_screenshot_baseline.json, so
the gate is a ratchet on named pages rather than a bare count: a live page that is NOT in
the baseline and has no screenshot fails; a baseline entry that now has a screenshot (or
is held, or no longer exists) also fails until it is removed, so the list can only shrink.

Proven-against: current main with the four role pages set to state: on_main (red at 4 role
pages named by URL, beside the 23 already-known pages), arb.webp removed (red, /modules/arb
named as a new offender) and vendors.webp truncated to zero bytes (red, /modules/vendors
named as unreadable); green at the committed baseline with all three restored.

    python scripts/check_public_page_screenshots.py            # ratchet: fail on new/stale
    python scripts/check_public_page_screenshots.py --strict   # fail on EVERY missing screenshot
    python scripts/check_public_page_screenshots.py --count    # number of live pages missing one
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASELINE_FILE = ROOT / "scripts" / "public_page_screenshot_baseline.json"
FAMILIES = ("module", "role", "function-per-segment")
MIN_BYTES = 1024
MIN_WIDTH, MIN_HEIGHT = 320, 200


def _image_problem(shot: dict) -> str | None:
    """Why the file behind a screenshot dict is not a real image, or None if it is."""
    from PIL import Image

    path = ROOT / "app" / shot["url"].lstrip("/")
    if not path.is_file():
        return f"file missing ({shot['url']})"
    if path.stat().st_size < MIN_BYTES:
        return f"file is {path.stat().st_size} bytes, under {MIN_BYTES} ({shot['url']})"
    try:
        with Image.open(path) as im:
            im.load()
            width, height = im.size
    except Exception as exc:  # noqa: BLE001 - any decode failure means not a real image
        return f"not a decodable image ({shot['url']}): {exc}"
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        return f"image is {width}x{height}, under {MIN_WIDTH}x{MIN_HEIGHT} ({shot['url']})"
    return None


def find_offenders() -> dict[str, str]:
    """{url: reason} for every live module/role/use-case page without a real screenshot."""
    from app.services.public_pages import get_page_screenshot, load_feed_pages

    offenders: dict[str, str] = {}
    for page in load_feed_pages():
        if page.page_family not in FAMILIES:
            continue
        try:
            shot = get_page_screenshot(page)
        except Exception as exc:  # noqa: BLE001 - an unreadable image is a missing one
            offenders[page.url] = f"{page.page_family} page: screenshot could not be read: {exc}"
            continue
        if shot is None:
            status = page.front_matter.get("capture_status")
            offenders[page.url] = f"{page.page_family} page, capture_status={status}: no screenshot returned"
            continue
        problem = _image_problem(shot)
        if problem:
            offenders[page.url] = f"{page.page_family} page: {problem}"
    return offenders


def load_baseline() -> set[str]:
    if not BASELINE_FILE.is_file():
        return set()
    return set(json.loads(BASELINE_FILE.read_text(encoding="utf-8"))["urls"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strict", action="store_true", help="ignore the baseline; fail on every offender")
    parser.add_argument("--count", action="store_true", help="print the number of offenders and exit 0")
    args = parser.parse_args()

    from app.services.public_pages import load_feed_pages
    live = [p for p in load_feed_pages() if p.page_family in FAMILIES]
    if not live:
        print("no live module/role/use-case pages found: an empty scan proves nothing", file=sys.stderr)
        return 2

    offenders = find_offenders()
    if args.count:
        print(len(offenders))
        return 0

    known = set() if args.strict else load_baseline()
    new = sorted(u for u in offenders if u not in known)
    stale = sorted(u for u in known if u not in offenders)

    print(f"{len(live)} live module/role/use-case pages checked; {len(offenders)} without a real screenshot")
    if args.strict:
        for url in sorted(offenders):
            print(f"  MISSING {url}  -- {offenders[url]}")
    if new and not args.strict:
        print("live pages with no screenshot that are not on the known list:")
        for url in new:
            print(f"  MISSING {url}  -- {offenders[url]}")
    if stale:
        print("known-list entries that are now fine (screenshot present, page held or gone); remove them from "
              "scripts/public_page_screenshot_baseline.json:")
        for url in stale:
            print(f"  STALE   {url}")
    if new or stale:
        print("capture the screenshot (scripts/capture_screenshots.py --modules, registry in "
              "app/services/public_pages.py) or hold the page by setting its front-matter state off on_main.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
