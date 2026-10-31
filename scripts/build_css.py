#!/usr/bin/env python
"""Build the Tailwind stylesheet and the fingerprint manifest.

    python scripts/build_css.py           # production: minified + hashed + manifest
    python scripts/build_css.py --dev     # unminified, no hashing (fast)
    python scripts/build_css.py --watch   # rebuild on change (implies --dev)
    python scripts/build_css.py --check   # verify the committed CSS is up to date

Referenced by the ``build:css*`` scripts in package.json.

How this fits together
----------------------
``app/static/css/tailwind-output.css`` **is committed on purpose** so a fresh clone
and ``docker compose up`` render correctly with no Node toolchain — the Docker image
is Python-only. That means the committed file has to be regenerated whenever
template classes change, or the class you just wrote will not exist at runtime.
``--check`` exists so CI can catch exactly that.

Tailwind is invoked through the **standalone CLI binary** (no Node required), which
``.gitignore`` expects at ``scripts/bin/tailwindcss[.exe]``. Download it from
https://github.com/tailwindlabs/tailwindcss/releases (v3.x — this project's
``tailwind.config.js`` is v3 format). ``npx tailwindcss`` is used as a fallback if
Node happens to be available.

Fingerprinting contract
-----------------------
Production builds emit ``css/tailwind-output.<8-hex>.css`` alongside the stable
filename and write ``app/static/manifest.json`` mapping one to the other. That
manifest is consumed by the ``asset_url`` Jinja filter in
``app/_bootstrap/assets.py``; without an entry it falls back to an mtime query
string. Both the hashed copies and the manifest are gitignored build artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC = REPO_ROOT / "app" / "static"
CSS_DIR = STATIC / "css"
INPUT_CSS = CSS_DIR / "tailwind-input.css"
OUTPUT_CSS = CSS_DIR / "tailwind-output.css"
CONFIG = REPO_ROOT / "tailwind.config.js"
MANIFEST = STATIC / "manifest.json"
BIN_DIR = REPO_ROOT / "scripts" / "bin"

DOWNLOAD_HINT = """
Tailwind CLI not found. Install the standalone binary (no Node needed):

  1. Download the v3.x build for your platform from
       https://github.com/tailwindlabs/tailwindcss/releases
  2. Save it as:
       {target}
  3. On macOS/Linux: chmod +x {target}

Alternatively, with Node available, `npx tailwindcss` is used automatically.
""".strip()


def find_tailwind() -> list[str] | None:
    """Locate a Tailwind CLI: vendored binary, then PATH, then npx."""
    exe = "tailwindcss.exe" if platform.system() == "Windows" else "tailwindcss"
    vendored = BIN_DIR / exe
    if vendored.exists():
        return [str(vendored)]

    on_path = shutil.which("tailwindcss")
    if on_path:
        return [on_path]

    npx = shutil.which("npx")
    if npx:
        return [npx, "--yes", "tailwindcss@3"]
    return None


def content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def build(dev: bool, watch: bool) -> int:
    cli = find_tailwind()
    if cli is None:
        exe = "tailwindcss.exe" if platform.system() == "Windows" else "tailwindcss"
        print(DOWNLOAD_HINT.format(target=BIN_DIR / exe), file=sys.stderr)
        return 2

    if not INPUT_CSS.exists():
        print(f"error: missing input stylesheet {INPUT_CSS}", file=sys.stderr)
        return 2
    if not CONFIG.exists():
        print(f"error: missing {CONFIG}", file=sys.stderr)
        return 2

    cmd = [*cli, "--config", str(CONFIG), "--input", str(INPUT_CSS), "--output", str(OUTPUT_CSS)]
    if not dev:
        cmd.append("--minify")
    if watch:
        cmd.append("--watch")

    print(f"$ {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, cwd=REPO_ROOT)
    except KeyboardInterrupt:
        return 0
    if proc.returncode != 0:
        return proc.returncode

    size_kb = OUTPUT_CSS.stat().st_size / 1024
    print(f"built {OUTPUT_CSS.relative_to(REPO_ROOT)} ({size_kb:.1f} KB)")

    if dev or watch:
        # Dev builds skip fingerprinting; asset_url falls back to an mtime buster.
        return 0
    return write_manifest()


def write_manifest() -> int:
    """Emit the content-hashed copy and manifest.json."""
    digest = content_hash(OUTPUT_CSS)
    hashed_name = f"tailwind-output.{digest}.css"
    hashed_path = CSS_DIR / hashed_name

    # Remove superseded hashed copies so the directory doesn't accumulate builds.
    for stale in CSS_DIR.glob("tailwind-output.*.css"):
        if stale.name != hashed_name:
            stale.unlink()

    shutil.copy2(OUTPUT_CSS, hashed_path)

    manifest = {}
    if MANIFEST.exists():
        try:
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}
    manifest["css/tailwind-output.css"] = f"css/{hashed_name}"
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"fingerprinted -> css/{hashed_name}")
    print(f"manifest      -> {MANIFEST.relative_to(REPO_ROOT)}")
    return 0


def check() -> int:
    """Fail if rebuilding would change the committed stylesheet.

    Guards the failure mode the committed-CSS design creates: a template gains a
    Tailwind class, nobody rebuilds, and the class silently does not exist in
    production.
    """
    if not OUTPUT_CSS.exists():
        print(f"error: {OUTPUT_CSS.relative_to(REPO_ROOT)} is missing", file=sys.stderr)
        return 1

    before = OUTPUT_CSS.read_bytes()
    # A per-process backup path. This was a fixed ".css.checkbak", so two
    # concurrent --check runs raced: the second copied over the first's backup,
    # and whichever finished first unlinked it, leaving the other's restore step
    # to die with FileNotFoundError - and the working tree holding a fresh build
    # instead of the restored one. Observed with parallel agents; the failure is
    # silent enough to be mistaken for a genuine stale-CSS result.
    backup = OUTPUT_CSS.with_suffix(".css.checkbak.%d" % os.getpid())
    shutil.copy2(OUTPUT_CSS, backup)
    try:
        cli = find_tailwind()
        if cli is None:
            print("SKIP: Tailwind CLI unavailable; cannot verify the committed CSS.")
            print("      Install it (see --help) so this check can run.")
            return 0
        rc = build(dev=False, watch=False)
        if rc != 0:
            return rc
        after = OUTPUT_CSS.read_bytes()
    finally:
        shutil.copy2(backup, OUTPUT_CSS)
        backup.unlink(missing_ok=True)

    if before != after:
        print(
            "FAIL: the committed tailwind-output.css is stale — a rebuild changes it.\n"
            "      Run: python scripts/build_css.py   and commit the result.",
            file=sys.stderr,
        )
        return 1
    stale = manifest_problem()
    if stale:
        print("FAIL: " + stale + "\n      Run: python scripts/build_css.py   and commit the result.", file=sys.stderr)
        return 1
    print("committed tailwind-output.css and its manifest are up to date")
    return 0


def manifest_problem() -> str | None:
    """Why the fingerprinted copy the pages load is not the committed stylesheet, or None."""
    try:
        named = json.loads(MANIFEST.read_text(encoding="utf-8"))["css/tailwind-output.css"]
    except (OSError, ValueError, KeyError):
        return "app/static/manifest.json does not name the fingerprinted tailwind-output.css"
    hashed = STATIC / named
    if not hashed.exists():
        return "the manifest names %s, which is not in the tree" % named
    if hashed.read_bytes() != OUTPUT_CSS.read_bytes():
        return "the manifest names %s, which differs from tailwind-output.css" % named
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dev", action="store_true", help="unminified, skip fingerprinting")
    parser.add_argument("--watch", action="store_true", help="rebuild on change (implies --dev)")
    parser.add_argument("--check", action="store_true", help="verify committed CSS is current")
    args = parser.parse_args(argv)

    os.chdir(REPO_ROOT)
    if args.check:
        return check()
    return build(dev=args.dev or args.watch, watch=args.watch)


if __name__ == "__main__":
    sys.exit(main())
