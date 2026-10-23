#!/usr/bin/env python
"""A design register for the public pages, and a ratchet against drift, duplication and staleness.

    python scripts/check_design_register.py                  # check
    python scripts/check_design_register.py --count          # violation count only
    python scripts/check_design_register.py --entries        # print the register for a builder
    python scripts/check_design_register.py --update-baseline  # lower the baseline; never raises it

Why
---
Two open pull requests each defined the same public button size selector in the same stylesheet with
different values; neither author could see the other's diff. ``design/register.yml`` lists the tokens,
scales and public components that exist, and this script makes the list binding. It follows
``scripts/check_reuse_register.py``: the same per-file baseline that only falls, the same CLI, the same
message shape (what, where, what to reuse), and no dependency beyond what that script already uses.

What it checks (over the files under ``scope`` in the register only)
--------------------------------------------------------------------
duplication  A registered component selector defined more than once in the same media context, in any
             scope stylesheet. ``.public-site .public-btn--sm`` and ``.public-btn--sm`` are the same
             selector; the value does not matter.
drift        A literal colour, ``px`` length or font stack in a scope stylesheet that is not listed under
             ``allowed_literals``, and a ``raw_utility`` pattern in a scope template (a registered
             component rebuilt by hand from Tailwind utilities).
staleness    A register entry with nothing to point at: a token not declared where the register says, a
             component whose selector is not defined where the register says, a component never used by
             any scope template, a canonical file that is gone; and a ``public-*`` class used in a scope
             template that no register component covers.

Duplication, drift and uncovered classes are counted per file and per key in
``scripts/design_register_baseline.json``; a file may hold no more than its own baseline. Staleness of
a register entry is not baselined: it fails.

Exemption: ``design-ok: <reason>`` on the line, or the line above. A bare marker does not exempt.

Static: reads the register, the stylesheets and the templates. No application import, no database.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTER = Path("design") / "register.yml"
BASELINE = Path("scripts") / "design_register_baseline.json"

MARKER = re.compile(r"design-ok:\s*(?!\*/|#\}|-->)\S")
SCOPE_PREFIX = ".public-site "
CLASS_RX = re.compile(r"\.([A-Za-z_][\w-]*)")
PUBLIC_CLASS = re.compile(r"\bpublic-[a-z0-9]+(?:-[a-z0-9]+)*(?:--[a-z0-9-]+)?\b")
CLASS_ATTR = re.compile(r'class\s*=\s*"([^"]*)"')
LITERAL_PX = re.compile(r"(?<![\w.#-])-?\d+(?:\.\d+)?px\b")
LITERAL_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b(?![\w-])")
LITERAL_FN = re.compile(r"\b(?:rgba?|hsla?)\(\s*[0-9][^)]*\)")
FONT_FAMILY = re.compile(r"font-family\s*:\s*([^;}]+)")
NL = chr(10)


# ---------------------------------------------------------------- register


def load_register(root: Path, register: Path | None = None):
    """(data, problems). A missing or malformed register is a problem, never a pass."""
    path = register or (root / REGISTER)
    if not path.exists():
        return {}, [f"register {path.as_posix()} does not exist"]
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return {}, [f"register is not valid YAML: {exc}"]
    return data, validate(data)


def validate(data) -> list[str]:
    problems = []
    scope = data.get("scope") or {}
    if not scope.get("css") or not scope.get("templates"):
        problems.append("register needs scope.css and scope.templates")
    seen = set()
    for i, c in enumerate(data.get("components") or []):
        cid = c.get("id") if isinstance(c, dict) else None
        if not cid or not c.get("path") or not (c.get("selector") or c.get("macro")) or not c.get("reuse"):
            problems.append(f"component {cid or i}: needs id, path, selector or macro, and a reuse line")
        if cid in seen:
            problems.append(f"component {cid}: duplicate id")
        seen.add(cid)
    for r in data.get("raw_utility") or []:
        try:
            re.compile(r.get("regex", ""))
        except re.error as exc:
            problems.append(f"raw_utility {r.get('id')}: bad regex: {exc}")
        if r.get("component") not in seen:
            problems.append(f"raw_utility {r.get('id')}: component {r.get('component')!r} is not registered")
    for u in data.get("undecided") or []:
        if not (isinstance(u, dict) and u.get("id") and u.get("item") and u.get("why")):
            problems.append("every undecided item needs id, item and why")
    return problems


# ---------------------------------------------------------------- css parsing


def strip_comments(css: str) -> str:
    """Blank comments, keeping newlines so line numbers survive."""
    return re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), css, flags=re.S)


def parse_rules(css: str):
    """[(media, selector, body, line)] for every rule block. Nested at-rules other than @media are
    descended into with their own prelude as the context."""
    text = strip_comments(css)
    rules: list[tuple[str, str, str, int]] = []

    def walk(start: int, end: int, media: str):
        i = start
        while i < end:
            j = i
            while j < end and text[j] not in "{;}":
                j += 1
            if j >= end or text[j] != "{":
                i = j + 1
                continue
            prelude = text[i:j].strip()
            depth, k = 1, j + 1
            while k < end and depth:
                depth += (text[k] == "{") - (text[k] == "}")
                k += 1
            body = text[j + 1:k - 1]
            if prelude.startswith("@"):
                walk(j + 1, k - 1, " ".join((media + " " + prelude).split()))
            elif prelude:
                line = text.count(NL, 0, i + (len(text[i:j]) - len(text[i:j].lstrip()))) + 1
                rules.append((media, prelude, body, line))
            i = k

    walk(0, len(text), "")
    return rules


def normalise(selector: str) -> str:
    s = " ".join(selector.split())
    while s.startswith(SCOPE_PREFIX):
        s = s[len(SCOPE_PREFIX):]
    s = s.replace(" .public-site ", " ")
    return s


def selectors_of(prelude: str):
    return [normalise(p) for p in prelude.split(",") if p.strip()]


def registered_classes(data) -> dict[str, str]:
    """{class name: component id} for every selector and modifier the register owns."""
    out = {}
    for c in data.get("components") or []:
        for sel in [c.get("selector")] + list(c.get("modifiers") or []):
            if sel:
                out[sel.lstrip(".")] = c["id"]
    return out


# ---------------------------------------------------------------- files


def _expand(root: Path, entries, suffixes) -> list[Path]:
    out = []
    for e in entries or []:
        p = root / e
        if p.is_dir():
            out += [q for q in sorted(p.rglob("*")) if q.suffix in suffixes]
        elif p.is_file():
            out.append(p)
    return out


def css_files(root, data):
    return _expand(root, (data.get("scope") or {}).get("css"), {".css"})


def template_files(root, data):
    return _expand(root, (data.get("scope") or {}).get("templates"), {".html"})


def _exempt(lines: list[str], idx: int) -> bool:
    if MARKER.search(lines[idx]):
        return True
    return idx > 0 and lines[idx - 1].lstrip().startswith(("/*", "{#", "#")) and bool(MARKER.search(lines[idx - 1]))


# ---------------------------------------------------------------- checks


def find_duplicates(root: Path, data):
    """{key: {file: [line]}} where key is `component/selector @media`."""
    owned = registered_classes(data)
    seen: dict[tuple[str, str], list[tuple[str, int, str]]] = defaultdict(list)
    for path in css_files(root, data):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.split(NL)
        for media, prelude, body, line in parse_rules(text):
            if _exempt(lines, line - 1):
                continue
            for sel in selectors_of(prelude):
                if any(c in owned for c in CLASS_RX.findall(sel)):
                    seen[(media, sel)].append((rel, line, " ".join(body.split())))
    hits: dict[str, dict[str, list[int]]] = {}
    detail: dict[str, list[tuple[str, int, str]]] = {}
    for (media, sel), defs in seen.items():
        if len(defs) < 2:
            continue
        comp = next(owned[c] for c in CLASS_RX.findall(sel) if c in owned)
        key = f"{comp}/{sel}" + (f" @{media}" if media else "")
        detail[key] = defs
        # every definition beyond the first counts, attributed to the file and line that added it
        for rel, line, _ in defs[1:]:
            hits.setdefault(key, {}).setdefault(rel, []).append(line)
    return hits, detail


def _allowed(data) -> tuple[set, set, set, list]:
    a = data.get("allowed_literals") or {}
    norm = lambda v: " ".join(str(v).split())  # noqa: E731
    return ({norm(x) for x in a.get("px") or []}, {norm(x) for x in a.get("colors") or []},
            {norm(x) for x in a.get("font_stacks") or []}, [norm(x) for x in a.get("shadows") or []])


def find_drift(root: Path, data):
    """{kind/value: {file: [line]}} for literals outside the register, plus raw-utility hits."""
    ok_px, ok_colors, ok_fonts, ok_shadows = _allowed(data)
    hits: dict[str, dict[str, list[int]]] = {}

    def add(key, rel, line):
        bucket = hits.setdefault(key, {}).setdefault(rel, [])
        if line not in bucket:
            bucket.append(line)

    for path in css_files(root, data):
        rel = path.relative_to(root).as_posix()
        raw = path.read_text(encoding="utf-8", errors="replace")
        lines = raw.split(NL)
        for n, line in enumerate(strip_comments(raw).split(NL), 1):
            if _exempt(lines, n - 1):
                continue
            if "@media" in line:
                line = re.sub(r"@media[^{]*", "", line)
            squeezed = " ".join(line.split())
            for shadow in ok_shadows:  # a registered elevation is one value, not a set of literals
                squeezed = squeezed.replace(shadow, "")
            line = squeezed
            for m in LITERAL_PX.findall(line):
                if m not in ok_px:
                    add(f"px/{m}", rel, n)
            for m in LITERAL_HEX.findall(line):
                add(f"color/{m.lower()}", rel, n)
            for m in LITERAL_FN.findall(line):
                if " ".join(m.split()) not in ok_colors:
                    add(f"color/{' '.join(m.split())}", rel, n)
            for m in FONT_FAMILY.findall(line):
                v = " ".join(m.split())
                if v not in ok_fonts and not v.startswith("var("):
                    add(f"font-family/{v}", rel, n)
    for r in data.get("raw_utility") or []:
        rx = re.compile(r["regex"])
        for path in template_files(root, data):
            if path.suffix not in set(r.get("suffixes") or [".html"]):
                continue
            rel = path.relative_to(root).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            lines = text.split(NL)
            for m in rx.finditer(text):
                n = text.count(NL, 0, m.start()) + 1
                if not _exempt(lines, n - 1):
                    add(f"raw-utility/{r['id']}", rel, n)
    return hits


def find_uncovered(root: Path, data):
    """public-* classes used in scope templates that no register component covers."""
    owned = registered_classes(data)
    hits: dict[str, dict[str, list[int]]] = {}
    for path in template_files(root, data):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.split(NL)
        for m in CLASS_ATTR.finditer(text):
            n = text.count(NL, 0, m.start()) + 1
            for cls in m.group(1).split():
                if PUBLIC_CLASS.fullmatch(cls) and cls not in owned and cls != "public-site" \
                        and not _exempt(lines, n - 1):
                    bucket = hits.setdefault(f"uncovered/{cls}", {}).setdefault(rel, [])
                    if n not in bucket:
                        bucket.append(n)
    return hits


def check_stale(root: Path, data) -> list[str]:
    """Register entries that point at nothing. Not baselined: the register must not rot."""
    fails = []
    css_text = {p.relative_to(root).as_posix(): strip_comments(p.read_text(encoding="utf-8", errors="replace"))
                for p in css_files(root, data)}
    tpl_text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in template_files(root, data))
    used = set()
    for m in CLASS_ATTR.finditer(tpl_text):
        used.update(m.group(1).split())
    for m in re.finditer(r"""['"]([^'"\n]*public-[^'"\n]*)['"]""", tpl_text):
        used.update(m.group(1).split())
    parked = {u.get("component") for u in data.get("undecided") or []}
    for group in (data.get("tokens") or {}).values():
        for t in group:
            f = root / t["path"]
            if not f.is_file():
                fails.append(f"[token {t['id']}] {t['path']} no longer exists; update design/register.yml")
                continue
            body = f.read_text(encoding="utf-8", errors="replace")
            for name in t["names"]:
                if not re.search(r"(?m)(^|[\s;{])" + re.escape(name) + r"\s*:", body):
                    fails.append(f"[token {t['id']}] {name} is not declared in {t['path']}; update the register")
    for c in data.get("components") or []:
        f = root / c["path"]
        if not f.is_file():
            fails.append(f"[component {c['id']}] {c['path']} no longer exists; update the register")
            continue
        if c.get("macro"):
            if not re.search(r"{%-?\s*macro\s+" + re.escape(c["macro"]) + r"\s*\(", f.read_text(encoding="utf-8")):
                fails.append(f"[component {c['id']}] macro {c['macro']} is no longer defined in {c['path']}")
            continue
        names = [c["selector"]] + list(c.get("modifiers") or [])
        for sel in names:
            cls = sel.lstrip(".")
            if not re.search(r"\." + re.escape(cls) + r"(?![\w-])", css_text.get(c["path"], "")):
                fails.append(f"[component {c['id']}] {sel} is not defined in {c['path']}; update the register")
        if c["selector"].lstrip(".") not in used and c["id"] not in parked:
            fails.append(f"[component {c['id']}] {c['selector']} is used by no template in scope; "
                         f"remove the entry or list it under undecided")
    return fails


# ---------------------------------------------------------------- baseline


def counts_of(hits) -> dict[str, dict[str, int]]:
    return {k: {f: len(v) for f, v in sorted(files.items())} for k, files in sorted(hits.items()) if files}


def load_baseline(root: Path, baseline: Path | None = None) -> dict:
    path = baseline or (root / BASELINE)
    return json.loads(path.read_text(encoding="utf-8")).get("patterns", {}) if path.exists() else {}


def excess_of(root: Path, files: dict, base: dict) -> dict[str, int]:
    growth = {rel: len(v) - base.get(rel, 0) for rel, v in files.items() if len(v) > base.get(rel, 0)}
    moved = sum(n for rel, n in base.items() if not (root / rel).exists())
    return {} if sum(growth.values()) <= moved else growth


def all_hits(root: Path, data):
    dups, detail = find_duplicates(root, data)
    merged = {}
    for prefix, group in (("duplicate", dups), ("drift", find_drift(root, data)),
                          ("coverage", find_uncovered(root, data))):
        for k, v in group.items():
            merged[f"{prefix}:{k}"] = v
    return merged, detail


def compare(data, hits, detail, baseline, root: Path):
    fails, slack = [], []
    comps = {c["id"]: c for c in data.get("components") or []}
    for key, files in sorted(hits.items()):
        growth = excess_of(root, files, baseline.get(key, {}))
        if not growth:
            continue
        where = ", ".join(f"{rel}:{n}" for rel in sorted(growth) for n in sorted(files[rel])[-growth[rel]:])
        kind, _, rest = key.partition(":")
        if kind == "duplicate":
            comp = comps.get(rest.split("/", 1)[0], {})
            sel = rest.split("/", 1)[1]
            defs = "; ".join(f"{r}:{ln} {{ {body} }}" for r, ln, body in detail.get(rest, []))
            fails.append(
                f"[{key}] second definition of {sel} at {where}.\n"
                f"      definitions: {defs}\n"
                f"      reuse:   {str(comp.get('reuse', '')).strip()}\n"
                f"      where:   {comp.get('path', '?')} ({comp.get('selector', comp.get('macro', '?'))})\n"
                f"      One selector, one rule. Edit the canonical rule, or `design-ok: <reason>`.")
        elif key.startswith("drift:raw-utility/"):
            rule = next((r for r in data.get("raw_utility") or [] if r["id"] == key.split("/", 1)[1]), {})
            fails.append(
                f"[{key}] {rule.get('concept', 'hand-built component')} at {where}.\n"
                f"      reuse:   {str(rule.get('reuse', '')).strip()}\n"
                f"      where:   component {rule.get('component')} (python scripts/check_design_register.py --entries)")
        elif kind == "drift":
            fails.append(
                f"[{key}] literal outside the register at {where}.\n"
                f"      Use a token or a value from the scales in design/register.yml, add the value to "
                f"allowed_literals with its place in a scale, or `design-ok: <reason>` on the line.")
        else:
            fails.append(
                f"[{key}] class used at {where} that no register component covers.\n"
                f"      Add it to the component it belongs to in design/register.yml, or use a registered one: "
                f"python scripts/check_design_register.py --entries")
    current = counts_of(hits)
    for key, files in sorted(baseline.items()):
        for rel, n in sorted(files.items()):
            if current.get(key, {}).get(rel, 0) < n:
                slack.append(f"baseline {key} {rel}: {n} -> {current.get(key, {}).get(rel, 0)} "
                             f"(run --update-baseline to lower)")
    return fails, slack


def update_baseline(root: Path, data, baseline_path: Path | None = None):
    path = baseline_path or (root / BASELINE)
    hits, _ = all_hits(root, data)
    current = counts_of(hits)
    old = load_baseline(root, path) if path.exists() else None
    messages = []
    if old is not None:
        for key, files in current.items():
            grown = excess_of(root, {r: [0] * n for r, n in files.items()}, old.get(key, {}))
            if grown:
                messages.append(f"refused: {key} would rise in {', '.join(sorted(grown))}")
        if messages:
            messages.append("The baseline only falls. Use the registered component or token, or register "
                            "the value in design/register.yml.")
            return False, messages
    for key, files in (old or {}).items():
        for rel, n in files.items():
            if current.get(key, {}).get(rel, 0) < n:
                messages.append(f"lowered {key} {rel}: {n} -> {current.get(key, {}).get(rel, 0)}")
    path.write_text(json.dumps({
        "_comment": ("Duplicate, drift and uncovered-class hits that already existed in the public surface, "
                     "per key and file. Written by scripts/check_design_register.py --update-baseline, which "
                     "refuses to raise a count or add a file. Lowering is routine."),
        "patterns": current,
    }, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return True, messages or ["baseline written"]


# ---------------------------------------------------------------- run


def run(root: Path, register: Path | None = None, baseline: Path | None = None):
    """(failures, notes)."""
    data, problems = load_register(root, register)
    if problems:
        return problems, []
    hits, detail = all_hits(root, data)
    more, slack = compare(data, hits, detail, load_baseline(root, baseline), root)
    return check_stale(root, data) + more, slack


def print_entries(data) -> None:
    for c in data.get("components") or []:
        print(f"{c['id']:24s} {c['concept']}")
        print(f"    use: {c['path']} :: {c.get('selector') or c.get('macro')}"
              + (f" ({', '.join(c.get('modifiers') or [])})" if c.get("modifiers") else ""))
        print(f"    {str(c['reuse']).strip()}")
    for u in data.get("undecided") or []:
        print(f"undecided: {u['id']}: {u['item']} (decides: {u.get('decides', '?')})")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=REPO)
    parser.add_argument("--count", action="store_true")
    parser.add_argument("--entries", action="store_true")
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.entries or args.update_baseline:
        data, problems = load_register(root)
        if problems:
            print("\n".join(problems))
            return 1
        if args.entries:
            print_entries(data)
            return 0
        ok, messages = update_baseline(root, data)
        print("\n".join(messages))
        return 0 if ok else 1
    fails, notes = run(root)
    if args.count:
        print(len(fails))
        return 1 if fails else 0
    for n in notes:
        print(f"  note: {n}")
    for f in fails:
        print(f"  {f}")
    print(f"\ndesign register: {len(fails)} violation(s)")
    if fails:
        print("The register lists what exists: python scripts/check_design_register.py --entries")
    print(len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
