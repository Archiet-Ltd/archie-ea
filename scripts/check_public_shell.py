#!/usr/bin/env python
"""Public-shell conformance: the navbar, its menus and the footer of entelim.org.

The public shell is rendered from one registry (content/site/registry.yml, read by
app/services/site_registry.py). This gate keeps it that way and keeps it on the
north-star look. It scans the templates named in ``public_scope`` in
design/patterns.json (the navbar, the menu macros and the footer) and fails on the
first violation: new components start at zero and stay there.

Rules (the public-page checker rules of the brand and navigation spec that apply to
the shell; the capability-page rules 9 to 11 belong to the capability template):

  PUBLIC_COLOUR_LITERAL   hex, rgb(), hsl(), a named or raw-palette colour class
  PUBLIC_ARBITRARY_VALUE  a Tailwind arbitrary value in brackets, e.g. w-[640px]
  PUBLIC_INLINE_CODE      a style attribute, a <style> block, a <script> with no src
  PUBLIC_ICON_SET         an inline <svg>, an emoji, a data-lucide name outside public_icons
  PUBLIC_PARALLEL_SYSTEM  public-btn, theme-marketing and the other marketing-system classes
  PUBLIC_LITERAL_LINK     an href that is a literal path instead of url_for(...) or a registry value
  PUBLIC_NAV_CONTRACT     skip link, labelled nav, disclosure buttons with matching panels,
                          Coming soon last and marked, no role="menu"
  PUBLIC_FOOTER_CONTRACT  <footer> with a labelled Site map nav that loops over the registry
                          rows, no link-styled span, at most 12 links per column
  PUBLIC_DARK_VARIANT     a dark: prefix on a colour utility (the tokens switch themselves)

Usage:
    python scripts/check_public_shell.py            # list findings, exit 1 if any
    python scripts/check_public_shell.py --count    # print the number of findings
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = ROOT / "design" / "patterns.json"
REGISTRY = ROOT / "content" / "site" / "registry.yml"
LAYOUT = ROOT / "app" / "templates" / "layouts" / "public_base.html"

CLASS_ATTR = re.compile(r'class\s*=\s*("([^"]*)"|\'([^\']*)\')', re.DOTALL)
HEX = re.compile(r"(?<![\w&/-])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![\w-])")
FUNC_COLOUR = re.compile(r"\b(?:rgb|rgba|hsl|hsla)\(")
RAW_PALETTE = re.compile(
    r"\b(?:[a-z-]+:)*(?:bg|text|border|ring|divide|from|via|to|fill|stroke)-"
    r"(?:white|black|(?:slate|gray|grey|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|"
    r"cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3})\b"
)
ARBITRARY = re.compile(r"\b[a-z0-9:-]+-\[[^\]\s]+\]")
PARALLEL = re.compile(
    r"\bpublic-(?:btn|eyebrow|callout|nav-link|navbar|footer|inner-page)\b|\btheme-marketing\b|\bhome-hero__"
)
DARK_COLOUR = re.compile(r"\bdark:(?!hidden\b|block\b)(?:bg|text|border|ring|from|via|to|divide|fill|stroke)-")
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐⬆]")
HREF = re.compile(r'\bhref\s*=\s*"([^"]*)"')
SVG = re.compile(r"<svg\b([^>]*)>", re.IGNORECASE)
LUCIDE = re.compile(r'data-lucide\s*=\s*"([^"{}]+)"')
SCRIPT = re.compile(r"<script\b([^>]*)>", re.IGNORECASE)


def _line(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _strip_comments(text: str) -> str:
    """Blank Jinja comments, keeping the line count."""
    return re.sub(r"\{#.*?#\}", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.DOTALL)


def scope() -> tuple[list[Path], set[str]]:
    data = json.loads(PATTERNS.read_text(encoding="utf-8"))
    return [ROOT / p for p in data.get("public_scope", [])], set(data.get("public_icons", []))


def scan(path: Path, icons: set[str]) -> list[tuple[int, str, str]]:
    raw = path.read_text(encoding="utf-8")
    text = _strip_comments(raw)
    out: list[tuple[int, str, str]] = []

    for match in CLASS_ATTR.finditer(text):
        value = match.group(2) if match.group(2) is not None else match.group(3)
        # Jinja expressions inside a class value are evaluated server side; scan
        # the literal parts only.
        literal = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " ", value, flags=re.DOTALL)
        at = _line(text, match.start())
        for pattern, check, what in (
            (HEX, "PUBLIC_COLOUR_LITERAL", "hex colour"),
            (FUNC_COLOUR, "PUBLIC_COLOUR_LITERAL", "rgb()/hsl() colour"),
            (RAW_PALETTE, "PUBLIC_COLOUR_LITERAL", "named or raw-palette colour class"),
            (ARBITRARY, "PUBLIC_ARBITRARY_VALUE", "arbitrary value"),
            (PARALLEL, "PUBLIC_PARALLEL_SYSTEM", "marketing-system class"),
            (DARK_COLOUR, "PUBLIC_DARK_VARIANT", "dark: colour variant"),
        ):
            for hit in pattern.finditer(literal):
                out.append((at, check, f"{what}: {hit.group(0)}"))
        if "rounded-full" in literal and "border" in literal and "px-4" in literal:
            out.append((at, "PUBLIC_PARALLEL_SYSTEM", "hand-rolled pill (use the badge macro)"))

    for hit in re.finditer(r"\sstyle\s*=", text):
        out.append((_line(text, hit.start()), "PUBLIC_INLINE_CODE", "style attribute"))
    for hit in re.finditer(r"<style\b", text, re.IGNORECASE):
        out.append((_line(text, hit.start()), "PUBLIC_INLINE_CODE", "<style> block"))
    for hit in SCRIPT.finditer(text):
        attrs = hit.group(1)
        if "src=" not in attrs and "application/ld+json" not in attrs:
            out.append((_line(text, hit.start()), "PUBLIC_INLINE_CODE", "inline <script>"))

    for hit in SVG.finditer(text):
        if "data-lucide" not in hit.group(1):
            out.append((_line(text, hit.start()), "PUBLIC_ICON_SET", "inline <svg>"))
    for hit in LUCIDE.finditer(text):
        if hit.group(1) not in icons:
            out.append((_line(text, hit.start()), "PUBLIC_ICON_SET", f"icon {hit.group(1)!r} is not in public_icons"))
    for hit in EMOJI.finditer(text):
        out.append((_line(text, hit.start()), "PUBLIC_ICON_SET", "emoji"))

    for hit in HREF.finditer(text):
        value = hit.group(1).strip()
        if value.startswith(("{{", "#", "mailto:")) or value == "":
            continue
        out.append((_line(text, hit.start()), "PUBLIC_LITERAL_LINK", f"literal href {value!r}"))

    name = path.name
    if name == "public_navbar.html":
        out += _nav_contract(path, text)
    if name == "public_footer.html":
        out += _footer_contract(path, text)
    return out


def _nav_contract(path: Path, text: str) -> list[tuple[int, str, str]]:
    out: list[tuple[int, str, str]] = []

    def need(ok: bool, what: str) -> None:
        if not ok:
            out.append((1, "PUBLIC_NAV_CONTRACT", what))

    layout = LAYOUT.read_text(encoding="utf-8") if LAYOUT.exists() else ""
    need('href="#main-content"' in layout and 'id="main-content"' in layout,
         "the public layout lacks a skip link to #main-content")
    need("data-site-nav" in text, "the header is not marked data-site-nav")
    # The navigation is drawn with the library's navigation_menu macro, which takes the label as an attribute.
    need(re.search(r'<nav\s+aria-label="[^"]+"', text) is not None
         or re.search(r"navigation_menu\([^%]*'aria-label':\s*'[^']+'", text) is not None, "no <nav aria-label>")
    need('role="menu"' not in text, 'role="menu" is banned: use the disclosure pattern')
    need('aria-label="Menu"' in text or "aria_label='Menu'" in text, "the Menu button has no accessible name")
    need('aria-label="Account menu"' in text or "aria_label='Account menu'" in text,
         "the account button has no accessible name")
    # The library's trigger macros: each is a type=button disclosure with aria-controls matching a panel id.
    for macro in re.finditer(r"(?:navigation_menu_trigger|sheet_trigger)\(([^%]*?)\) %\}", text):
        attrs = macro.group(1)
        need("type='button'" in attrs, "a disclosure trigger macro is not type=button")
        controls = re.search(r"aria_controls='([^']+)'", attrs)
        need(controls is not None, "a disclosure trigger macro has no aria_controls")
        if controls:
            target = controls.group(1)
            need(f"id='{target}'" in text or f"id='{target}' " in text or f"id={target!r}" in text, f"aria_controls {target!r} matches no panel id")
    for button in re.finditer(r"<button\b([^>]*)>", text):
        attrs = button.group(1)
        if "aria-expanded" in attrs:
            need('type="button"' in attrs, "a disclosure trigger is not <button type=button>")
            controls = re.search(r'aria-controls="([^"]+)"', attrs)
            need(controls is not None, "a disclosure trigger has no aria-controls")
            if controls:
                target = controls.group(1)
                # ids are written literally or built from a loop variable
                literal = re.sub(r"\{\{.*?\}\}", "", target)
                if "{{" not in target:
                    need(f'id="{target}"' in text, f"aria-controls {target!r} matches no panel id")
                else:
                    need(f'id="{literal}' in text, f"aria-controls {target!r} matches no panel id")
    soon = text.find("soon_group(")
    delivered = text.find("menu_group(")
    need(soon == -1 or (delivered != -1 and soon > delivered),
         "the Coming soon group must come after the delivered groups")
    menu_path = path.parent / "public_menu.html"
    if not menu_path.exists():  # a fixture directory: fall back to the real menu macros
        menu_path = ROOT / "app" / "templates" / "components" / "public_menu.html"
    menu = menu_path.read_text(encoding="utf-8")
    need('data-availability="early_access"' in menu, "Coming soon group is not marked data-availability")
    need("Coming soon" in menu or "label" in menu, "Coming soon group has no heading")
    return out


def _footer_contract(path: Path, text: str) -> list[tuple[int, str, str]]:
    out: list[tuple[int, str, str]] = []

    def need(ok: bool, what: str) -> None:
        if not ok:
            out.append((1, "PUBLIC_FOOTER_CONTRACT", what))

    need("<footer" in text, "no <footer> element")
    need('<nav aria-label="Site map"' in text, 'the footer lacks <nav aria-label="Site map">')
    need("reg.footer.rows" in text, "the footer does not loop over the registry rows")
    for hit in re.finditer(r"<span\b[^>]*class=\"[^\"]*(?:underline|hover:text-foreground|cursor-pointer)[^\"]*\"", text):
        out.append((_line(text, hit.start()), "PUBLIC_FOOTER_CONTRACT", "a link-styled span"))
    if REGISTRY.exists():
        import yaml

        reg = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
        columns = {g["label"]: len(g["items"]) for g in reg["product"]["groups"]}
        columns["Coming soon"] = len(reg["product"]["coming_soon"]["items"]) + 1
        columns["Solutions"] = len(reg["solutions"]["items"]) + len(reg["solutions"]["coming_soon"]["items"])
        columns["Roles"] = len(reg["roles"]["families"]) + 1
        columns["Compare"] = len(reg["compare"]["items"]) + 1
        for group in reg["resources"]["groups"]:
            columns[group["label"]] = len(group["items"])
        for label, count in columns.items():
            if count > 12:
                out.append((1, "PUBLIC_FOOTER_CONTRACT", f"footer column {label!r} has {count} links (limit 12)"))
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", action="store_true", help="print the number of findings only")
    parser.add_argument("files", nargs="*", help="scan these files instead of public_scope")
    args = parser.parse_args(argv)

    files, icons = scope()
    if args.files:
        files = [Path(f) for f in args.files]
    findings: list[tuple[str, int, str, str]] = []
    for path in files:
        if not path.exists():
            findings.append((str(path.relative_to(ROOT)) if path.is_absolute() else str(path), 0,
                             "PUBLIC_NAV_CONTRACT", "file named in public_scope does not exist"))
            continue
        for line, check, detail in scan(path, icons):
            findings.append((str(path.relative_to(ROOT)) if path.is_absolute() and ROOT in path.parents else str(path),
                             line, check, detail))
    if args.count:
        print(len(findings))
        return 0
    for template, line, check, detail in findings:
        print(f"{template}:{line}: {check}: {detail}")
    if findings:
        print(f"{len(findings)} public-shell finding(s)")
        return 1
    print("public shell: no findings")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
