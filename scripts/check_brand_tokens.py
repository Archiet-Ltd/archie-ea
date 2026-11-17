#!/usr/bin/env python
"""The brand colour decision, enforced.

app/static/css/shadcn_tokens.css is the only place colour is defined. This
checker reads it and fails when a measured pair drops below its threshold, and
counts the old brand blues hard-coded anywhere else.

    python scripts/check_brand_tokens.py            # contrast failures (last line = count)
    python scripts/check_brand_tokens.py --raw      # hard-coded brand blues outside the token file and logo
    python scripts/check_brand_tokens.py --report   # every measured ratio

Pairs, each in light and dark: the label on --primary 4.5:1; --input on the page
and on the card 3:1 (control boundaries); each of the five chart steps on the
card 3:1; --brand equal to --primary, so the logo is the one brand blue.

Proven-against: --input in the token file changed to `0 0% 85%` (a border under
3:1) and --brand changed away from --primary; tests/test_brand_tokens.py runs both
and the count rises from 0.
"""

from __future__ import annotations

import colorsys
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKENS = ROOT / "app/static/css/shadcn_tokens.css"

RAW_BLUES = re.compile(r"#(?:2563eb|135fbc)\b|rgba?\(\s*(?:37,\s*99,\s*235|29,\s*78,\s*216)\b", re.I)
# The token file and the logo are the only places a brand blue may be written out.
RAW_EXEMPT = (
    "app/static/css/shadcn_tokens.css",
    "app/static/brand/",
    "app/static/favicon.svg",
    "app/templates/partials/_brand_logo.html",
    # Source templates for code generated for customers, not this product's UI.
    "app/modules/solutions_product/templates/",
    "app/static/css/tailwind-output",
    "tests/",
)
RAW_SUFFIXES = {".html", ".css", ".js", ".py", ".svg", ".j2"}

THRESHOLDS = [
    # (label, foreground token, background token, minimum)
    ("label on primary", "primary-foreground", "primary", 4.5),
    ("input on page", "input", "background", 3.0),
    ("input on card", "input", "card", 3.0),
] + [(f"chart-{i} on card", f"chart-{i}", "card", 3.0) for i in range(1, 6)]


def _block(css: str, selector: str) -> dict[str, tuple[float, float, float]]:
    """HSL triplets declared in the every `selector { ... }` block."""
    out = {}
    for m in re.finditer(r"(?m)^\s*" + re.escape(selector) + r"\s*\{(.*?)^\s*\}", css, re.S):
        for name, h, s, l in re.findall(
            r"--([a-z0-9-]+):\s*([\d.]+)\s+([\d.]+)%\s+([\d.]+)%", m.group(1)
        ):
            out[name] = (float(h), float(s), float(l))
    return out


def _rgb(hsl: tuple[float, float, float]) -> tuple[float, float, float]:
    h, s, l = hsl
    return colorsys.hls_to_rgb(h / 360, l / 100, s / 100)


def _lum(hsl) -> float:
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in _rgb(hsl))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def measure(css: str):
    """Yield (theme, label, ratio, minimum) and (theme, label, None, None) for a missing token."""
    for theme, selector in (("light", ":root"), ("dark", ".dark")):
        tokens = _block(css, selector)
        for label, fg, bg, minimum in THRESHOLDS:
            if fg not in tokens or bg not in tokens:
                yield theme, label, None, minimum
            else:
                yield theme, label, contrast(tokens[fg], tokens[bg]), minimum
        same = tokens.get("brand") is not None and tokens.get("brand") == tokens.get("primary")
        yield theme, "brand equals primary", 1.0 if same else None, 1.0


def contrast_failures(css: str) -> list[str]:
    bad = []
    for theme, label, ratio, minimum in measure(css):
        if ratio is None or ratio < minimum:
            shown = "missing" if ratio is None else f"{ratio:.2f}"
            bad.append(f"{theme}: {label} is {shown}, needs {minimum}")
    return bad


def raw_blues() -> list[str]:
    hits = []
    for path in sorted(ROOT.glob("app/**/*")) + sorted(ROOT.glob("scripts/**/*")):
        rel = path.relative_to(ROOT).as_posix()
        if not path.is_file() or path.suffix not in RAW_SUFFIXES or rel.startswith(RAW_EXEMPT):
            continue
        if rel == "scripts/check_brand_tokens.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if RAW_BLUES.search(line):
                hits.append(f"{rel}:{n}")
    return hits


def main() -> int:
    css = TOKENS.read_text(encoding="utf-8")
    if "--raw" in sys.argv:
        hits = raw_blues()
        for h in hits:
            print(h, file=sys.stderr)
        print(len(hits))
        return 0
    if "--report" in sys.argv:
        for theme, label, ratio, minimum in measure(css):
            print(f"{theme:5} {label:22} {'missing' if ratio is None else f'{ratio:.2f}'} (min {minimum})")
    bad = contrast_failures(css)
    for b in bad:
        print(b, file=sys.stderr)
    print(len(bad))
    return 0


if __name__ == "__main__":
    sys.exit(main())
