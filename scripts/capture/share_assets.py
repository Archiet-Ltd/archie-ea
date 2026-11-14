# -*- coding: utf-8 -*-
"""Cut every module recording into the files that are shared, and write the share manifest.

    python -m scripts.capture.share_assets            # every recording
    python -m scripts.capture.share_assets --only applications

For each recording in ``MODULE_RECORDINGS`` this makes, from the recording already captured (no second
recorder): a wide 16:9 file for the site and YouTube, a square file for LinkedIn and an upright file for Shorts
and Reels, each with its captions burned in; the link-preview cover (the recording's outcome frame); and one
``manifest.json`` that a distribution tool reads from the public site. Run it again after a recording is replaced:
``tests/test_share_manifest.py`` fails while a shape was cut from an older recording than the one in the page.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib

from app.services.origin import canonical_origin
from app.services.public_pages import (
    IMG_MODULES_DIR,
    MODULE_RECORDINGS,
    MODULE_RECORDINGS_MANIFEST,
    SHARE_DIR,
    SHARE_MANIFEST,
    SHARE_URL_PREFIX,
)
from scripts.capture.clip_kit import (
    SHAPES,
    caption_intervals,
    cover_image,
    cut_shape,
    ffprobe_duration_seconds,
)

SHARE_WEB_PATH = "/static/video/share"


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sentence(text: str) -> str:
    return text if text.endswith((".", "?", "!")) else text + "."


def build_entry(slug: str, recorded: dict, out: pathlib.Path) -> dict:
    """Cut one recording and return its manifest entry."""
    entry = MODULE_RECORDINGS[slug]
    video = IMG_MODULES_DIR / f"{slug}.webm"
    captions, closing = recorded["captions"], recorded["closing"][0]
    intervals = caption_intervals(video, len(captions))
    files = {}
    for shape, (width, height) in SHAPES.items():
        dest = out / f"{slug}-{shape}.mp4"
        cut_shape(video, dest, shape, captions, intervals)
        files[shape] = {"path": f"{SHARE_WEB_PATH}/{dest.name}", "bytes": dest.stat().st_size,
                        "duration_seconds": round(ffprobe_duration_seconds(dest), 1),
                        "width": width, "height": height}
    cover = out / f"{slug}-cover.jpg"
    cover_image(IMG_MODULES_DIR / f"{slug}.poster.webp", cover)
    spoken = [entry["title"]] + captions[1:] + [closing]
    return {
        "slug": slug,
        "title": entry["title"],
        "message": entry["promise"],
        "summary": entry["summary"],
        "captions": captions,
        "caption_seconds": [[round(a, 1), round(b, 1)] for a, b in intervals],
        "transcript": " ".join(_sentence(line) for line in spoken),
        "claims": [_sentence(line) for line in spoken],
        "cover": f"{SHARE_WEB_PATH}/{cover.name}",
        "files": files,
        "share_url": f"{canonical_origin()}{SHARE_URL_PREFIX}/{slug}",
        "module_url": f"{canonical_origin()}/modules/{slug}",
        "alt": entry["alt"],
        "source_sha256": recorded["clip_sha256"],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", default="", help="comma-separated slugs to cut (the others keep their entry)")
    args = parser.parse_args(argv)
    wanted = {s for s in args.only.split(",") if s}
    recorded = json.loads((IMG_MODULES_DIR / MODULE_RECORDINGS_MANIFEST).read_text(encoding="utf-8"))
    manifest_path = SHARE_DIR / SHARE_MANIFEST
    kept = {}
    if manifest_path.is_file():
        kept = {e["slug"]: e for e in json.loads(manifest_path.read_text(encoding="utf-8"))["recordings"]}
    SHARE_DIR.mkdir(parents=True, exist_ok=True)
    for slug in sorted(MODULE_RECORDINGS):
        if slug not in recorded or (wanted and slug not in wanted):
            continue
        kept[slug] = build_entry(slug, recorded[slug], SHARE_DIR)
        print("  share  %-24s %s" % (slug, ", ".join("%s %d bytes" % (k, v["bytes"]) for k, v in kept[slug]["files"].items())))
    ordered = [kept[s] for s in sorted(kept) if s in MODULE_RECORDINGS]
    manifest_path.write_text(json.dumps({"site": canonical_origin(), "recordings": ordered}, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
