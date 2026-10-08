#!/usr/bin/env python
"""Air-gap gate: fail when the UI loads resources from the public internet.

    python scripts/check_external_origins.py            # full report
    python scripts/check_external_origins.py --count    # violation count only
    python scripts/check_external_origins.py --max N    # fail if count exceeds N
    python scripts/check_external_origins.py FILE ...   # scan specific files

Why
---
Archie is being prepared for deployment inside an enterprise network. Corporate
networks block or proxy public CDNs, so any ``<script src="https://cdn...">`` is a
page that breaks on a managed workstation — or a firewall exception request per
domain, each of which costs weeks. Every such asset must be vendored into
``app/static/`` and served from the same origin.

This is also a privacy control: an outbound request leaks the referring URL, the
client IP and the fact of use to a third party, which is exactly what a DPO review
looks for.

What counts as a violation
--------------------------
An external origin used to *load a resource*: script, stylesheet, font, image,
fetch/XHR, or dynamic import. Deliberately NOT counted:

* ``www.w3.org`` and friends in ``xmlns=`` — XML namespace identifiers are never
  fetched over the network, and there are ~300 of them in inline SVG.
* Documentation/placeholder hosts (``example.com``, ``your-idp.example.com``).
* Plain anchor links to external sites — a link a user may click is not a resource
  the page loads.
* ``<link rel="canonical" ...>`` and ``<link rel="alternate" ...>`` — these tell a
  crawler which URL is authoritative; the browser never fetches them, so they are
  not a resource load either, same reasoning as the plain-anchor exclusion above.
* ALLOWED_ORIGINS below — identity providers and similar, which are external by
  design and are reached by the browser, not embedded as assets.
"""

from __future__ import annotations

import argparse
import glob
import re
import sys
from collections import Counter

# Origins that are legitimately external. Identity providers must be reachable
# from the browser; that is inherent to federated SSO, not an air-gap violation.
ALLOWED_ORIGINS = {
    "login.microsoftonline.com",
    "login.windows.net",
    "sts.windows.net",
}

# Never fetched: XML namespace URIs, and documentation placeholders.
IGNORED_ORIGINS = {
    "www.w3.org", "w3.org", "schemas.xmlsoap.org", "purl.org", "xmlns.com",
    "example.com", "api.example.com", "your-idp.example.com", "www.example.com",
    "cognito-idp.REGION.amazonaws.com", "localhost", "127.0.0.1",
}

# Contexts that constitute loading a resource from an origin.
RESOURCE_PATTERNS = [
    ("script", re.compile(r"""<script[^>]+src\s*=\s*["']\s*(https?://[^"'\s>]+)""", re.I)),
    ("script", re.compile(r"""\bimport\s*\(?\s*["'](https?://[^"']+)["']""")),
    # Excludes rel="canonical"/"alternate": those are crawler metadata, never
    # fetched by the browser, regardless of whether rel appears before or
    # after href in the tag (the lookahead scans the whole tag, not just the
    # prefix before href).
    ("style",  re.compile(
        r"""<link(?![^>]*\brel\s*=\s*["'](?:canonical|alternate)["'])[^>]+href\s*=\s*["']\s*(https?://[^"'\s>]+)""",
        re.I,
    )),
    ("style",  re.compile(r"""@import\s+(?:url\()?["']?(https?://[^"')\s]+)""", re.I)),
    ("style",  re.compile(r"""url\(\s*["']?(https?://[^"')\s]+)""", re.I)),
    ("image",  re.compile(r"""<img[^>]+(?:src|:src)\s*=\s*["'][^"']*?(https?://[^"'\s>+]+)""", re.I)),
    ("xhr",    re.compile(r"""(?:fetch|axios(?:\.\w+)?)\s*\(\s*["'`](https?://[^"'`]+)""")),
]

ORIGIN_OF = re.compile(r"https?://([a-zA-Z0-9._-]+)")


def scan_file(path: str) -> list[tuple[int, str, str, str]]:
    """Return [(line_no, kind, origin, url)] of external resource loads."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return []

    findings = []
    for kind, pattern in RESOURCE_PATTERNS:
        for match in pattern.finditer(text):
            url = match.group(1)
            origin_match = ORIGIN_OF.match(url)
            if not origin_match:
                continue
            origin = origin_match.group(1)
            if origin in IGNORED_ORIGINS or origin in ALLOWED_ORIGINS:
                continue
            line_no = text.count("\n", 0, match.start()) + 1
            if "air-gap-ok" in text.splitlines()[line_no - 1]:
                continue
            findings.append((line_no, kind, origin, url))
    return findings


def default_paths() -> list[str]:
    return sorted(
        glob.glob("app/templates/**/*.html", recursive=True)
        + glob.glob("app/static/js/**/*.js", recursive=True)
        + glob.glob("app/static/css/**/*.css", recursive=True)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--count", action="store_true", help="print only the total")
    parser.add_argument("--max", type=int, default=None, help="fail if the count exceeds this")
    parser.add_argument("--by-url", action="store_true", help="group by asset URL (vendoring worklist)")
    args = parser.parse_args(argv)

    paths = args.paths or default_paths()
    paths = [p for p in paths if p.endswith((".html", ".js", ".css", ".jinja", ".jinja2"))]

    all_findings: list[tuple[str, int, str, str, str]] = []
    for path in paths:
        for line_no, kind, origin, url in scan_file(path):
            all_findings.append((path, line_no, kind, origin, url))

    total = len(all_findings)

    if args.count:
        print(total)
        return 0

    if args.by_url:
        by_url = Counter(f[4] for f in all_findings)
        kinds = {f[4]: f[2] for f in all_findings}
        print(f"{len(by_url)} distinct external assets to vendor:\n")
        for url, count in by_url.most_common():
            print(f"  {count:>3}x  [{kinds[url]:<6}] {url}")
    elif all_findings:
        for path, line_no, kind, origin, url in all_findings[:60]:
            print(f"{path}:{line_no}: [{kind}] {url}")
        if total > 60:
            print(f"... and {total - 60} more")

    by_origin = Counter(f[3] for f in all_findings)
    print(f"\n{total} external resource load(s) across {len(paths)} file(s), "
          f"{len(by_origin)} origin(s).")
    for origin, count in by_origin.most_common(10):
        print(f"    {count:>4}  {origin}")

    if total:
        print("\nVendor these into app/static/ and reference them with url_for('static', ...).")
        print("Mark a genuinely-required external load with an 'air-gap-ok' comment on the line.")

    if args.max is not None and total > args.max:
        print(f"\nFAIL: {total} exceeds the allowed maximum of {args.max}.")
        return 1
    if args.max is None and total:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
