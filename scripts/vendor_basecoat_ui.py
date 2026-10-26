#!/usr/bin/env python
"""Vendor the rest of flask-basecoat's component styles and behaviour into Entelim.

    python scripts/vendor_basecoat_ui.py /path/to/flask-basecoat [--ref b327186c2]

Entelim's app/static/css/basecoat_components.css holds the .cn-* rules the Overview dashboard needed (62 of the
library's class names). Every other library component (input, dropdown menu, select, tabs, dialog, pagination and
the rest) rendered unstyled, and nothing gave the menus behaviour. This script appends, in a second cascade layer,
every library rule whose selector names at least one .cn-* class that the existing file does not already style.
Rules that only name classes the existing file styles are left alone, so nothing the Overview renders can change.
Colour tokens become hsl(var(--token)) because this product stores colours as hue, saturation and lightness
triples. The Tailwind 4 variables the rules compose from are renamed --cn-tw-* and registered with the library's
own @property defaults: this product's Tailwind 3 output resets every --tw-* variable on every element outside any
cascade layer, which would otherwise erase each layered shadow, ring, blur and animation value.

It also writes app/static/vendor/basecoat/flask-basecoat-ui.js: the library runtime core plus the menu, popover,
select, tabs, dialog, disclosure and toggle behaviours, which act only on elements carrying the library's data-slot
names.
"""
import argparse
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSS_OUT = ROOT / "app/static/css/basecoat_components.css"
JS_OUT = ROOT / "app/static/vendor/basecoat/flask-basecoat-ui.js"
MARK = "/* ---- added by scripts/vendor_basecoat_ui.py"

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("library")
    ap.add_argument("--ref", default="b327186c2")
    a = ap.parse_args()
    lib = git_show(a.library, a.ref, "flask_basecoat/static/css/flask-basecoat.min.css")
    current = CSS_OUT.read_text(encoding="utf-8")
    if MARK in current:
        current = current[:current.index(MARK)].rstrip() + "\n"
    have = set(re.findall(r"\.cn-([a-z0-9-]+)", current))

    rules, used_tokens, used_frames = [], set(), set()
    for wrappers, sel, body in walk(lib):
        names = set(re.findall(r"\.cn-([a-z0-9-]+)", sel))
        if not names or not (names - have):
            continue
        if any(w.startswith("@layer properties") for w in wrappers):
            continue
        outer = [w for w in wrappers if not w.startswith("@layer")]
        text = f"{sel}{{{colourise(body)}}}"
        for w in reversed(outer):
            text = f"{w}{{{text}}}"
        rules.append(text)
        used_tokens |= set(re.findall(r"var\((--[a-z0-9-]+)", body))
        used_frames |= set(re.findall(r"animation(?:-name)?:\s*([\w-]+)", body))

    # tokens the rules read that this product does not define anywhere
    defined = set()
    for f in (ROOT / "app/static/css").glob("*.css"):
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
    frame_text = [frames[f] for f in sorted(used_frames) if f in frames and f"@keyframes {f}" not in tw_entelim
                  and f"@keyframes {f}" not in current]

    registered = re.findall(r"@property --tw-[a-z0-9-]+\{[^}]*\}", lib)

    def rename(css):
        return css.replace("--tw-", "--cn-tw-")
    runtime = git_show(a.library, a.ref, "app/assets/styles/runtime.css")
    out = [current.rstrip(), "",
           f"{MARK} from flask-basecoat {a.ref}: every other component's .cn-* rules ({len(rules)} rules), "
           "and the library's runtime rules that hide closed menus, dialogs and inactive tabs. ---- */",
           runtime.strip(),
           ":root{" + ";".join(f"{t}:{theme[t]}" for t in missing) + ("" if not missing else ";") + "--color-black:black}",
           *map(rename, registered),
           *map(rename, frame_text),
           "@layer basecoat-ui {",
           *map(rename, rules),
           "}", ""]
    CSS_OUT.write_text("\n".join(out), encoding="utf-8")

    js = [f"/* flask-basecoat {a.ref}: runtime core and the menu, popover, select, tabs, dialog, disclosure and toggle "
          "behaviours, vendored by scripts/vendor_basecoat_ui.py. They act only on elements carrying the library's "
          "data-slot names. */"]
    for part in JS_PARTS:
        js.append(f"/* ---- {part} ---- */")
        js.append(git_show(a.library, a.ref, f"flask_basecoat/static/js/src/{part}.js"))
    JS_OUT.parent.mkdir(parents=True, exist_ok=True)
    JS_OUT.write_text("\n".join(js), encoding="utf-8")
    print(f"css: kept {len(have)} names, added {len(rules)} rules, tokens added {missing}, keyframes {len(frame_text)}, registered {len(registered)}")
    print(f"js: {JS_OUT.relative_to(ROOT)} {JS_OUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()
