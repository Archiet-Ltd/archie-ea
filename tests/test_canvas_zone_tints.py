"""The canvas zone bands wear Entelim's own brand chart tokens, and the text on every tint is readable.

Reads the token file the browser loads, so it checks what a person sees: five steps that look different in each mode, the
brand blue first, and the foreground text at 70 percent holding 4.5:1 on each band tint (the chart colour at 10 percent
over the card)."""

import colorsys
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKENS = (ROOT / "app/static/css/shadcn_tokens.css").read_text(encoding="utf8")
CANVAS = ROOT / "app/templates/components/canvas"
SPEC_LIGHT = [(0.497, 0.163, 257), (0.66, 0.16, 55), (0.45, 0.10, 160), (0.64, 0.10, 230), (0.40, 0.14, 320)]


def _block(selector):
    """The colour-scheme block for the selector: the one that declares --card inside its own braces.

    The token file opens with a short :root block (heat tokens, closed on its own last line) before the palette block, so
    the palette block is found by its contents, not by being the first block with --card somewhere nearby."""
    lines = TOKENS.split("\n")
    for start, line in enumerate(lines):
        if line.strip() != selector + " {":
            continue
        out = []
        for line in lines[start:]:
            out.append(line)
            if line == "  }":
                break
        block = "\n".join(out)
        if "--card:" in block:
            return block
    raise AssertionError(f"no {selector} block in the token file declares --card")


def _tokens(selector):
    return {k: tuple(float(x.strip("%")) for x in v.split())
            for k, v in re.findall(r"--([a-z0-9-]+):\s*([\d.]+ [\d.]+% [\d.]+%);", _block(selector))}


def _rgb(hsl):
    h, s, light = hsl
    return colorsys.hls_to_rgb(h / 360, light / 100, s / 100)


def _oklch_to_rgb(lightness, chroma, hue):
    a, b = chroma * math.cos(math.radians(hue)), chroma * math.sin(math.radians(hue))
    l_, m_, s_ = lightness + 0.3963377774 * a + 0.2158037573 * b, lightness - 0.1055613458 * a - 0.0638541728 * b, lightness - 0.0894841775 * a - 1.2914855480 * b
    cone_l, cone_m, cone_s = l_ ** 3, m_ ** 3, s_ ** 3
    lin = (4.0767416621 * cone_l - 3.3077115913 * cone_m + 0.2309699292 * cone_s, -1.2684380046 * cone_l + 2.6097574011 * cone_m - 0.3413193965 * cone_s,
           -0.0041960863 * cone_l - 0.7034186147 * cone_m + 1.7076147010 * cone_s)
    return tuple(min(1, max(0, 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055)) for v in lin)


def _luminance(rgb):
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _mix(fg, bg, alpha):
    return tuple(alpha * f + (1 - alpha) * g for f, g in zip(fg, bg))


def test_light_chart_tokens_are_the_brand_ramp():
    tokens = _tokens(":root")
    for i, spec in enumerate(SPEC_LIGHT, 1):
        want = _oklch_to_rgb(*spec)
        got = _rgb(tokens[f"chart-{i}"])
        assert max(abs(w - g) for w, g in zip(want, got)) < 0.02, f"chart-{i} is not the brand ramp step {spec}"


def test_each_mode_has_five_different_steps_and_the_brand_blue_first():
    for selector in (":root", ".dark"):
        tokens = _tokens(selector)
        steps = [tokens[f"chart-{i}"] for i in range(1, 6)]
        assert tokens["chart-1"][:3] == tokens["primary"][:3], f"{selector}: chart-1 is not the brand blue"
        for i in range(5):
            for j in range(i + 1, 5):
                apart = abs(steps[i][0] - steps[j][0]) > 20 or abs(steps[i][2] - steps[j][2]) > 5
                assert apart, f"{selector}: chart-{i + 1} and chart-{j + 1} look the same"


def test_text_on_every_zone_band_holds_4_5_to_1():
    for selector in (":root", ".dark"):
        tokens = _tokens(selector)
        card, fg = _rgb(tokens["card"]), _rgb(tokens["foreground"])
        for zone in range(1, 6):
            band = _mix(_rgb(tokens[f"chart-{zone}"]), card, 0.10)
            assert _contrast(_mix(fg, band, 0.70), band) >= 4.5, f"{selector} zone {zone}"


def test_every_box_zone_is_a_chart_token_the_frame_tints():
    frame = (CANVAS / "_frame.html").read_text(encoding="utf8")
    boxes = json.loads((CANVAS / "_boxes.json").read_text(encoding="utf8"))
    for zone in range(1, 6):
        assert f"{zone}: 'bg-chart-{zone}/10 border-chart-{zone}/30'" in frame
    for canvas in ("business-model-canvas", "value-proposition-canvas"):
        for key, meta in boxes[canvas].items():
            assert meta["zone"] in range(1, 6), f"{canvas}/{key}"


def test_box_titles_and_icons_are_full_foreground_and_hold_4_5_to_1_in_dark_and_light():
    """The title and its icon are drawn in the foreground token itself (not muted, not an inherited dark colour), and
    that token reads at 4.5:1 or better on every band tint in dark mode as well as light."""
    frame = (CANVAS / "_frame.html").read_text(encoding="utf8")
    band = frame[frame.index("{% macro band("):frame.index("{%- endmacro %}", frame.index("{% macro band("))]
    assert "ui.card_title(class='flex items-center gap-2 text-foreground')" in band
    assert "ui.lucide(b.icon, 'size-4 shrink-0 text-foreground')" in band
    for selector in (":root", ".dark"):
        tokens = _tokens(selector)
        card, fg = _rgb(tokens["card"]), _rgb(tokens["foreground"])
        for zone in range(1, 6):
            tint = _mix(_rgb(tokens[f"chart-{zone}"]), card, 0.10)
            assert _contrast(fg, tint) >= 4.5, f"{selector} zone {zone} title"
