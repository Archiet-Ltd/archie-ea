#!/usr/bin/env python
"""Vendor flask-basecoat's component styles and behaviour into Entelim.

    python scripts/vendor_basecoat_ui.py /path/to/flask-basecoat [--ref b327186c2]

Entelim's app/static/css/basecoat_components.css holds the .cn-* rules the Overview dashboard needed (62 of the
library's class names), in a cascade layer; this script leaves it alone. Every other page rendered library
components unstyled, and nothing gave the menus behaviour.

This script writes app/static/css/basecoat_ui.css with every library .cn-* rule, outside any cascade layer, because
this product's Tailwind 3 output is unlayered too and its reset (no borders, transparent buttons) would beat any
layered rule. The scope is written :where(.style-vega), so a rule weighs the same as one utility class: that beats
the reset, and because the head partial loads the file before Tailwind, a utility class on the same element still
wins, as in the library. Colour tokens become hsl(var(--token)) because this product stores colours as hue,
saturation and lightness triples. The Tailwind 4 variables the rules compose from are renamed --cn-tw-* and
registered with the library's own @property defaults, since Tailwind 3 resets every --tw-* variable on every element.
The library's runtime rules (closed menus, dialogs and inactive tabs hidden) are copied as the library ships them.

It also writes app/static/vendor/basecoat/flask-basecoat-ui.js: the library runtime core plus the menu, popover,
select, tabs, dialog, disclosure and toggle behaviours, which act only on elements carrying the library's data-slot
names.
"""
import argparse
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSS_OUT = ROOT / "app/static/css/basecoat_ui.css"
JS_OUT = ROOT / "app/static/vendor/basecoat/flask-basecoat-ui.js"

COLOUR_TOKENS = {
    "background", "foreground", "card", "card-foreground", "popover", "popover-foreground", "primary",
    "primary-foreground", "secondary", "secondary-foreground", "muted", "muted-foreground", "accent",
    "accent-foreground", "destructive", "destructive-foreground", "border", "input", "ring",
    "sidebar", "sidebar-foreground", "sidebar-primary", "sidebar-primary-foreground", "sidebar-accent",
    "sidebar-accent-foreground", "sidebar-border", "sidebar-ring",
    "chart-1", "chart-2", "chart-3", "chart-4", "chart-5",
}
JS_PARTS = ["core", "components/menu", "components/popover", "components/select", "components/tabs",
            "components/dialog", "components/disclosure", "components/toggles"]
# The library's base layer hides [hidden] elements whatever their display; its components and their contents rely
# on it. The sidebar keeps this product's own rules (its hidden sub-menus still take their gap, as the approved layout has it).
HIDDEN_RULE = ("@layer components{:is([data-slot], [data-slot] *)[hidden]:not([hidden=until-found])"
               ":not([data-slot=sidebar] *){display:none!important}}")


def git_show(repo, ref, path):
    return subprocess.check_output(["git", "-C", str(repo), "show", f"{ref}:{path}"], text=True)


def walk(css):
    """Yield (wrappers, selector, body) for every style rule, with enclosing at-rule preludes."""
    i, n, stack = 0, len(css), []
    while i < n:
        j = css.find("{", i)
        k = css.find("}", i)
        if k != -1 and (j == -1 or k < j):
            if stack:
                stack.pop()
            i = k + 1
            continue
        if j == -1:
            break
        prelude = css[i:j].strip()
        if prelude.startswith("@") and not prelude.startswith(("@font-face", "@property", "@keyframes", "@page")):
            stack.append(prelude)
            i = j + 1
            continue
        depth, m = 1, j + 1
        while m < n and depth:
            if css[m] == "{":
                depth += 1
            elif css[m] == "}":
                depth -= 1
            m += 1
        yield list(stack), prelude, css[j + 1:m - 1]
        i = m


def keyframes(css):
    return {m.group(1): m.group(0) for m in re.finditer(r"@keyframes ([\w-]+)\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", css)}


def colourise(body):
    def rep(m):
        name = m.group(1)
        return f"hsl(var(--{name}))" if name in COLOUR_TOKENS else m.group(0)
    return re.sub(r"var\(--([a-z0-9-]+)\)", rep, body)


def rename(css):
    return css.replace("--tw-", "--cn-tw-")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("library")
    ap.add_argument("--ref", default="b327186c2")
    a = ap.parse_args()
    lib = git_show(a.library, a.ref, "flask_basecoat/static/css/flask-basecoat.min.css")

    # class names this product already styles in its own stylesheets (the sidebar shell, for one) stay with them
    own = set()
    for f in list((ROOT / "app/static/css").glob("*.css")) + list((ROOT / "app/static/styles").glob("*.css")):
        if f.name not in (CSS_OUT.name, "basecoat_components.css") and not f.name.startswith("tailwind-output"):
            own |= set(re.findall(r"\.cn-([a-z0-9-]+)", f.read_text(encoding="utf-8", errors="ignore")))
    # the Overview's 62 names keep the Overview's own rules on the page that holds it
    overview = set(re.findall(r"\.cn-([a-z0-9-]+)", (ROOT / "app/static/css/basecoat_components.css").read_text(encoding="utf-8")))

    rules, used_tokens, used_frames = [], set(), set()
    for wrappers, sel, body in walk(lib):
        names = set(re.findall(r"\.cn-([a-z0-9-]+)", sel))
        if not names or names & own:
            continue
        if any(w.startswith("@layer properties") for w in wrappers):
            continue
        outer = [w for w in wrappers if not w.startswith("@layer")]
        scope = (":where(.style-vega:not(:has([data-testid=overview-dashboard]), [data-testid=overview-dashboard])) "
                 if names & overview
                 else ":where(.style-vega) ")
        text = f"{sel.replace('.style-vega ', scope)}{{{colourise(body)}}}"
        for w in reversed(outer):
            text = f"{w}{{{text}}}"
        rules.append(text)
        used_tokens |= set(re.findall(r"var\((--[a-z0-9-]+)", body))
        used_frames |= set(re.findall(r"animation(?:-name)?:\s*([\w-]+)", body))

    # theme tokens the rules read that this product does not define anywhere
    defined = set()
    for f in (ROOT / "app/static/css").glob("*.css"):
        if f.name not in (CSS_OUT.name, "basecoat_components.css"):  # the second loads on the Overview only
            defined |= set(re.findall(r"(--[a-z0-9-]+)\s*:", f.read_text(encoding="utf-8", errors="ignore")))
    theme = {}
    for wrappers, sel, body in walk(lib):
        if sel.startswith(":root") and any(w.startswith("@layer theme") for w in wrappers):
            for d in body.split(";"):
                if ":" in d and d.strip().startswith("--"):
                    k, v = d.split(":", 1)
                    theme[k.strip()] = v.strip()
    missing = sorted(t for t in used_tokens if t not in defined and t[2:] not in COLOUR_TOKENS
                     and not t.startswith("--tw-") and t in theme)
    frames = keyframes(lib)
    tw_entelim = (ROOT / "app/static/css/tailwind-output.css").read_text(encoding="utf-8", errors="ignore")
    frame_text = [frames[f] for f in sorted(used_frames) if f in frames and f"@keyframes {f}" not in tw_entelim]
    registered = re.findall(r"@property --tw-[a-z0-9-]+\{[^}]*\}", lib)
    runtime = git_show(a.library, a.ref, "app/assets/styles/runtime.css")

    out = [f"/* Written by scripts/vendor_basecoat_ui.py from flask-basecoat {a.ref}: every component's .cn-* rules "
           f"({len(rules)}), and the library's runtime rules that hide closed menus, dialogs and inactive tabs. */",
           runtime.strip(),
           HIDDEN_RULE,
           ":root{" + "".join(f"{t}:{theme[t]};" for t in missing) + "}",
           *map(rename, registered),
           *map(rename, frame_text),
           *map(rename, rules),
           ""]
    CSS_OUT.write_text("\n".join(out), encoding="utf-8")

    js = [f"/* flask-basecoat {a.ref}: runtime core and the menu, popover, select, tabs, dialog, disclosure and toggle "
          "behaviours, vendored by scripts/vendor_basecoat_ui.py. They act only on elements carrying the library's "
          "data-slot names. */"]
    for part in JS_PARTS:
        js.append(f"/* ---- {part} ---- */")
        js.append(git_show(a.library, a.ref, f"flask_basecoat/static/js/src/{part}.js"))
    JS_OUT.parent.mkdir(parents=True, exist_ok=True)
    JS_OUT.write_text("\n".join(js), encoding="utf-8")
    print(f"css: {CSS_OUT.relative_to(ROOT)} {len(rules)} rules, left to this product: {sorted(own)}, tokens added {missing}, "
          f"keyframes {len(frame_text)}, registered {len(registered)}")
    print(f"js: {JS_OUT.relative_to(ROOT)} {JS_OUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()
