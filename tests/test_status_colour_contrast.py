"""Status text colours and fills meet WCAG contrast in both themes.

Plain pytest, no browser: parses the HSL tokens from shadcn_tokens.css for
:root (light) and .dark, computes WCAG 2.x contrast ratios and checks the
tailwind mapping and the native-control rules that make dark mode legible.
"""

import colorsys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKENS_CSS = (ROOT / "app/static/css/shadcn_tokens.css").read_text(encoding="utf-8")
TAILWIND = (ROOT / "tailwind.config.js").read_text(encoding="utf-8")


def _strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _tokens(selector):
    css = _strip_comments(TOKENS_CSS)
    out = {}
    for m in re.finditer(r"(?m)^[ \t]*" + re.escape(selector) + r"[ \t]*\{([^}]*)\}", css):
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", m.group(1)):
            out[name] = value.strip()
    return out


def _rgb(triplet):
    h, s, light = re.match(r"^\s*([\d.]+)\s+([\d.]+)%\s+([\d.]+)%", triplet).groups()
    r, g, b = colorsys.hls_to_rgb(float(h) / 360.0, float(light) / 100.0, float(s) / 100.0)
    return r, g, b


def _lum(rgb):
    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(theme, fg, bg):
    t = THEMES[theme]
    a, b = _lum(_rgb(t[fg])), _lum(_rgb(t[bg]))
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


LIGHT = _tokens(":root")
THEMES = {"light": LIGHT, "dark": {**LIGHT, **_tokens(".dark")}}


def _need(theme, fg, bg, minimum):
    r = ratio(theme, fg, bg)
    assert r >= minimum, f"{theme}: {fg} on {bg} is {r:.2f}:1, needs {minimum}:1"


def test_status_emphasis_text_reads_on_background_in_both_themes():
    for theme in ("light", "dark"):
        for name in ("destructive", "success", "warning"):
            _need(theme, f"--{name}-emphasis", "--background", 4.5)


def test_dark_destructive_fill_and_its_text():
    _need("dark", "--destructive", "--card", 3.0)
    _need("dark", "--destructive-foreground", "--destructive", 4.5)


def test_light_warning_fill_reads_on_background():
    _need("light", "--warning", "--background", 3.0)


def test_tailwind_text_colour_default_uses_the_emphasis_token():
    m = re.search(r"textColor\s*:\s*\{", TAILWIND)
    assert m, "tailwind.config.js has no textColor block"
    block = TAILWIND[m.end():]
    for name in ("destructive", "success", "warning"):
        d = re.search(name + r"\s*:\s*\{[^}]*?DEFAULT\s*:\s*\"hsl\(var\((--[\w-]+)\)", block)
        assert d, f"textColor.{name}.DEFAULT is not mapped"
        assert d.group(1) == f"--{name}-emphasis", f"textColor.{name}.DEFAULT uses {d.group(1)}"


def test_dark_theme_sets_color_scheme_and_select_reads_background():
    css = _strip_comments(TOKENS_CSS)
    assert re.search(r"\.dark\s*\{\s*color-scheme\s*:\s*dark\s*;", css), ".dark does not set color-scheme: dark"
    sel = re.search(r"(?s)(?:^|[}\s])select\s*,[^{]*\{([^}]*)\}", css)
    assert sel, "no rule styles select"
    assert re.search(r"background-color\s*:\s*hsl\(var\(--background\)\)", sel.group(1)), \
        "select does not take its background from --background"
