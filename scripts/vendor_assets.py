#!/usr/bin/env python
"""Fetch and verify the vendored front-end libraries.

    python scripts/vendor_assets.py            # download/refresh everything
    python scripts/vendor_assets.py --verify   # check files match the manifest

Why vendored
------------
Archie is deployed inside enterprise networks where public CDNs are blocked or
proxied. Every library the UI needs is therefore served from this origin. The
``air-gap`` gate in scripts/verify.py enforces that no template reintroduces an
external asset.

Why a manifest
--------------
"What is in your vendor directory and where did it come from?" is a question an
enterprise security review will ask. ASSETS below is the single source of truth:
one pinned version per library, no floating tags. ``--verify`` re-hashes every
file so a silently modified or truncated asset is detectable.

Never use a floating tag here. The original code referenced ``lucide@latest``,
which means any upstream compromise executes in the app on the next page load
with no review step.
"""

from __future__ import annotations

import hashlib
import os
import sys
import urllib.request

DEST = os.path.join("app", "static", "vendor")
MANIFEST = os.path.join(DEST, "VENDOR_MANIFEST.txt")

# filename -> pinned upstream URL. One version per library: before vendoring,
# chart.js was pinned three ways, d3 came from two origins, and alpine, dompurify
# and lucide each had two different pins live at once.
ASSETS = {
    "chart.umd.min.js":       "https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js",
    "d3.min.js":              "https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js",
    "d3-sankey.min.js":       "https://cdn.jsdelivr.net/npm/d3-sankey@0.12.3/dist/d3-sankey.min.js",
    "joint.min.js":           "https://cdn.jsdelivr.net/npm/jointjs@3.7.7/dist/joint.min.js",
    "joint.min.css":          "https://cdn.jsdelivr.net/npm/jointjs@3.7.7/dist/joint.min.css",
    "lodash.min.js":          "https://cdn.jsdelivr.net/npm/lodash@4.17.21/lodash.min.js",
    "backbone-min.js":        "https://cdn.jsdelivr.net/npm/backbone@1.4.1/backbone-min.js",
    "jquery.min.js":          "https://cdn.jsdelivr.net/npm/jquery@3.7.1/dist/jquery.min.js",
    "mermaid.min.js":         "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js",
    "dagre.min.js":           "https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js",
    "purify.min.js":          "https://cdn.jsdelivr.net/npm/dompurify@3.0.8/dist/purify.min.js",
    "alpine.min.js":          "https://cdn.jsdelivr.net/npm/alpinejs@3.14.3/dist/cdn.min.js",
    "alpine-focus.min.js":    "https://cdn.jsdelivr.net/npm/@alpinejs/focus@3.14.3/dist/cdn.min.js",
    "alpine-intersect.min.js": "https://cdn.jsdelivr.net/npm/@alpinejs/intersect@3.14.3/dist/cdn.min.js",
    "alpine-collapse.min.js": "https://cdn.jsdelivr.net/npm/@alpinejs/collapse@3.14.3/dist/cdn.min.js",
    "marked.min.js":          "https://cdn.jsdelivr.net/npm/marked/marked.min.js",
    "plot.umd.min.js":        "https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6.14/dist/plot.umd.min.js",
    "jspdf.umd.min.js":       "https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js",
    "html2canvas.min.js":     "https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js",
    "zxcvbn.js":              "https://cdnjs.cloudflare.com/ajax/libs/zxcvbn/4.2.0/zxcvbn.js",
    "lucide.min.js":          "https://unpkg.com/lucide@0.344.0/dist/umd/lucide.min.js",
    # Inter (SIL Open Font License 1.1) — brand typeface, ARCH-111. Static woff2
    # weights from @fontsource/inter, latin subset. Referenced via @font-face in
    # shadcn_tokens.css, never loaded from Google Fonts or any other CDN at
    # runtime — that is the whole point of vendoring it.
    "inter-400.woff2":        "https://cdn.jsdelivr.net/npm/@fontsource/inter@5.3.0/files/inter-latin-400-normal.woff2",
    "inter-500.woff2":        "https://cdn.jsdelivr.net/npm/@fontsource/inter@5.3.0/files/inter-latin-500-normal.woff2",
    "inter-600.woff2":        "https://cdn.jsdelivr.net/npm/@fontsource/inter@5.3.0/files/inter-latin-600-normal.woff2",
    "inter-700.woff2":        "https://cdn.jsdelivr.net/npm/@fontsource/inter@5.3.0/files/inter-latin-700-normal.woff2",
}

# Committed but not fetched by this script (pre-existing bundle; licence text
# accompanying the Inter font files, hashed manually rather than downloaded on
# every run since it never changes with a pinned font version).
# "basecoat" is a directory copied from the flask-basecoat repository (its chart script, with the source
# commit named in the file header), not downloaded from a CDN.
UNMANAGED = {"pptxgenjs.bundle.js", "inter-LICENSE.txt", "basecoat"}

MIN_BYTES = 500


def digest_of(data: bytes) -> str:
    return hashlib.sha384(data).hexdigest()[:16]


def write_manifest(rows):
    with open(MANIFEST, "w", encoding="utf-8", newline="") as fh:
        fh.write("# Vendored front-end libraries — provenance record.\n")
        fh.write("# Regenerate with: python scripts/vendor_assets.py\n")
        fh.write("# Verify with:     python scripts/vendor_assets.py --verify\n")
        fh.write("# filename | upstream URL | bytes | sha384 (first 16 hex)\n")
        for name, url, size, dg in sorted(rows):
            fh.write(f"{name} | {url} | {size} | {dg}\n")


def download() -> int:
    os.makedirs(DEST, exist_ok=True)
    rows, failed = [], []
    for name, url in ASSETS.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "archie-vendoring/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
        except Exception as exc:  # noqa: BLE001
            failed.append((name, str(exc)[:80]))
            continue
        if len(data) < MIN_BYTES:
            failed.append((name, f"implausibly small ({len(data)} bytes)"))
            continue
        with open(os.path.join(DEST, name), "wb") as fh:
            fh.write(data)
        rows.append((name, url, len(data), digest_of(data)))
        print(f"  {len(data):>9,}b  {name}")
    write_manifest(rows)
    print(f"\n{len(rows)} vendored, {len(failed)} failed; manifest written")
    for name, err in failed:
        print(f"  FAILED {name}: {err}")
    return 1 if failed else 0


def verify() -> int:
    if not os.path.exists(MANIFEST):
        print(f"FAIL: {MANIFEST} is missing — run without --verify to generate it")
        return 1
    expected = {}
    for line in open(MANIFEST, encoding="utf-8"):
        if line.startswith("#") or "|" not in line:
            continue
        name, url, size, dg = [x.strip() for x in line.split("|")]
        expected[name] = (url, int(size), dg)

    problems = []
    for name in sorted(set(ASSETS) | set(expected)):
        if name not in expected:
            problems.append(f"{name}: in ASSETS but absent from the manifest")
            continue
        if name not in ASSETS:
            problems.append(f"{name}: in the manifest but no longer in ASSETS")
            continue
        url, size, dg = expected[name]
        if url != ASSETS[name]:
            problems.append(f"{name}: manifest URL differs from ASSETS\n"
                            f"      manifest {url}\n      ASSETS   {ASSETS[name]}")
        path = os.path.join(DEST, name)
        if not os.path.exists(path):
            problems.append(f"{name}: file missing from {DEST}")
            continue
        data = open(path, "rb").read()
        if len(data) != size or digest_of(data) != dg:
            problems.append(f"{name}: content differs from manifest "
                            f"(manifest {dg}/{size}, actual {digest_of(data)}/{len(data)})")

    on_disk = {f for f in os.listdir(DEST)
               if not f.startswith(".") and f != os.path.basename(MANIFEST)}
    for extra in sorted(on_disk - set(ASSETS) - UNMANAGED):
        problems.append(f"{extra}: present on disk but not declared in ASSETS")

    if problems:
        print(f"FAIL: {len(problems)} problem(s) with the vendored assets:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"All {len(ASSETS)} vendored assets match the manifest.")
    return 0


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(root)
    sys.exit(verify() if "--verify" in sys.argv else download())
