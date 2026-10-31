#!/usr/bin/env python
"""Every page follows one designed block, and the design map says which.

406 page templates were built one task at a time, each hand-styled, so the
same job (a list, a record, a form) looks different from page to page. The
design map, docs/design-map.json, assigns every page template to one block
(dashboard, data table, record detail, form, settings, chat, board,
onboarding, public page, empty state). Each block names its reference (the
shadcn block or example and the flask-basecoat example it follows), the
design/patterns.json patterns it is built from, and the shared macros
(components/*.html, macros/*.html) a page in it may use.

The map is derived where it can be and designed where it must be:

  derived  extends, the shared macros a page imports or includes, and its
           controls counted by the component inventory generator
           (scripts/component_inventory.py, the one inventory);
  designed block, spec, status, extra_macros -- written by the designers
           and kept when the map is regenerated.

A page carries its block as a template marker on its own line:

    {# design-block: data-table #}

check_block() is the merge check for one changed page template: it must be
in the map with a block, carry the matching marker, and import only the
macros its block allows. A new page template must declare its block before
it can be added (--write takes the block from the marker).

    python scripts/check_design_map.py                 # whole tree: summary and problems
    python scripts/check_design_map.py --json          # whole tree, machine-readable
    python scripts/check_design_map.py --files a.html  # check_block on named files
    python scripts/check_design_map.py --base main     # check_block on templates changed since main
    python scripts/check_design_map.py --changed       # whole tree plus check_block on this branch's changes
    python scripts/check_design_map.py --pr 912        # dry run against an open pull request
    python scripts/check_design_map.py --write         # regenerate derived fields and the tracker

Whole-tree mode fails on a page missing from the map, a map entry with no
page, an unknown block, a marker disagreeing with the map, or a spec the map
names that does not exist. The pages outside their block (importing a macro
the block does not allow) and the pages with no marker are counted, and
scripts/verify.py holds both counts so they can only fall.

Proven-against: app/templates/applications/list_simple.html (data-table)
importing components/stepper.html with a data-table marker -- check_block
names the disallowed macro; the same file with a form marker names the
mismatch; a new admin/x.html with no map entry is refused as undeclared.
All three observed red, then clean once reverted.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

MAP_PATH = ROOT / "docs" / "design-map.json"
TRACKER_PATH = ROOT / "docs" / "design" / "tracker.html"
PATTERNS_PATH = ROOT / "design" / "patterns.json"

MARKER_RE = re.compile(r"{#-?\s*design-block:\s*([a-z][a-z-]*)\s*-?#}")
SHARED_PREFIXES = ("components/", "macros/")
STATUSES = ("mapped", "specified", "implemented", "verified")
DESIGNED_FIELDS = ("block", "basis", "spec", "status", "extra_macros", "note", "persona", "also_serves", "components",
                   "tier", "page_type", "type_changed", "original", "frame", "charts", "inhouse_components",
                   "confidence", "reason", "patterns", "states", "departures", "grid", "pattern_gap",
                   "data_types")
# The designers' port map: how close each page is to a shadcn original. A is the original verbatim,
# B is composed inside the nearest original's frame, C1 to C5 are the five forms shadcn has no original for.
TIERS = ("A", "B", "C1", "C2", "C3", "C4", "C5")
CONFIDENCE = ("high", "low")
# Final port map: the rules a page may depart from (criteria v4) and the content grids of rule R13.
DEPARTURE_RULES = ("R9", "R10", "brand", "C-form")
GRIDS = ("G1", "G2", "G3", "G4")

# Shared macro file stem -> the shadcn component it is an instance of. A macro with no
# shadcn counterpart is an app component named after its file (source "app").
SHADCN_ALIASES = {
    "alert": "alert", "avatar": "avatar", "badge": "badge", "status_badge": "badge",
    "breadcrumb": "breadcrumb", "breadcrumb_nav": "breadcrumb", "button": "button",
    "card": "card", "data_card": "card", "metrics_card": "card", "checkbox": "checkbox",
    "dialog": "dialog", "modal": "dialog", "modal_form": "dialog", "modal_standardized": "dialog",
    "confirmation_dialog": "alert-dialog", "drawer": "drawer", "dropdown_menu": "dropdown-menu",
    "empty_state": "empty", "form_field": "field", "input": "input", "search_bar": "input",
    "pagination_controls": "pagination", "progress": "progress", "radio_group": "radio-group",
    "select": "select", "skeleton": "skeleton", "loading_state": "spinner", "switch": "switch",
    "table": "table", "data_table": "table", "dense_table": "table", "workbench_table": "table",
    "tabs": "tabs",
}


# ------------------------------------------------------------------ reading

def _refs(rel, source):
    """Template references of one template, as Jinja names, via the inventory's own AST walker."""
    from component_inventory import extract_elements_from_template

    refs = extract_elements_from_template(rel, source, {}, {})[5]
    return refs


def page_facts(rel, source):
    """None when the template is not a page (it extends nothing); else what the map derives from it."""
    if "extends" not in source:
        return None
    refs = _refs(rel, source)
    if not refs["extends"]:
        return None
    used = refs["imports"] | refs["from_imports"] | refs["includes"]
    marker = MARKER_RE.search(source)
    return {
        "extends": sorted(refs["extends"]),
        "macros": sorted(m for m in used if isinstance(m, str) and m.startswith(SHARED_PREFIXES)),
        "marker": marker.group(1) if marker else None,
    }


def _all_templates():
    from check_template_syntax import template_files as _discover

    for _root, full in _discover():
        rel = Path(full).resolve().relative_to(ROOT).as_posix()
        if rel.startswith("app/"):
            yield rel


def tree_pages():
    """{repo path: page facts} for every page template under app/."""
    pages = {}
    for rel in _all_templates():
        source = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        facts = page_facts(rel, source)
        if facts is not None:
            pages[rel] = facts
    return pages


def load_map(path=MAP_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def duplicate_key_problems(path=MAP_PATH):
    """A key written twice in one object: json.loads keeps the last copy, so the first is dead text."""
    text = Path(path).read_text(encoding="utf-8")
    doubled = []

    def hook(pairs):
        seen = set()
        for key, _ in pairs:
            if key in seen and key not in doubled:
                doubled.append(key)
            seen.add(key)
        return dict(pairs)

    json.loads(text, object_pairs_hook=hook)
    lines = text.split("\n")
    problems = []
    for key in sorted(doubled):
        marker = json.dumps(key) + ":"
        at = [(n, len(line) - len(line.lstrip())) for n, line in enumerate(lines, 1) if line.lstrip().startswith(marker)]
        indents = sorted({i for _, i in at if sum(1 for _, j in at if j == i) > 1})
        same = [n for n, i in at if indents and i == indents[0]]
        where = f"lines {same[0]} and {same[1]}" if len(same) > 1 else "twice"
        problems.append(f"{key}: written twice in docs/design-map.json ({where}); only the last copy is read")
    return problems


def component_of(macro):
    """The component name a shared macro file is an instance of."""
    stem = Path(macro).stem
    return SHADCN_ALIASES.get(stem, stem.replace("_", "-"))


def macro_name(macro):
    """The name the port map gives an in-house component: its macro file stem, dashed."""
    return Path(macro).stem.replace("_", "-")


def inhouse_names(entry):
    """{in-house component name: verdict} a row lists, e.g. {'page-shell': 'Replace'}."""
    out = {}
    for item in entry.get("inhouse_components", []):
        name, _, verdict = item.rpartition(":")
        out[name.replace("_", "-")] = verdict
    return out


def inhouse_verdicts(design_map):
    """{in-house component name: verdict} over every row; the port map gives one verdict per component."""
    out = {}
    for entry in design_map["pages"].values():
        out.update(inhouse_names(entry))
    return out


def row_macros(design_map, entry):
    """The macro files a row stands for: those of the components it lists and of the in-house components it names."""
    vocabulary = design_map.get("components", {})
    macros = {m for name in entry.get("components", []) for m in vocabulary.get(name, {}).get("macros", [])}
    named = set(inhouse_names(entry))
    macros |= {m for slot in vocabulary.values() for m in slot.get("macros", []) if macro_name(m) in named}
    return sorted(macros)


def allowed_macros(design_map, entry):
    block = design_map["blocks"].get(entry.get("block"), {})
    return sorted(set(block.get("allowed_macros", [])) | set(entry.get("extra_macros", [])))


# ------------------------------------------------------------------ the tier checks

# Checks of criteria v2 that need a rendered golden original or a browser. They are listed, not faked.
PENDING_TIER_CHECKS = {
    "A2-A5": "structure, class, computed-style and pixel match against the original's golden render (no render of "
             "the site examples or preview cards yet)",
    "B2": "the page frame equals the nearest original's frame (needs the golden render)",
    "B4-B6": "every component node carries its pinned recipe and every other node is a layout or text element",
    "B7": "regions below the header, stat cards and primary tables counted from the rendered DOM",
    "B8": "frame pixel difference against the golden render",
    "B9": "each chart's family, series, tooltip and legend read from the rendered page",
    "X6-X10": "computed colours, fonts, accessibility findings and horizontal overflow in a browser",
    "C": "the form-specific check of each Tier C form (the marked region and its geometry)",
}
TIER_CHECKS = {
    "replace_in_use": "pages still using an in-house component the port map marks Replace",
    "in_house_beyond_tier": "pages using an in-house component their tier does not allow (A: any Compose or Keep; B: Keep)",
    "charts_over_two": "Tier A and B pages whose own template draws more than two charts",
    "colour_literals": "pages whose own template holds a colour literal instead of a token (X1)",
    "inline_style": "pages whose own template sets an inline style beyond the geometry properties (X2)",
    "arbitrary_values": "pages whose own template uses an arbitrary-value class such as h-[250px] (X4)",
    "pattern_outside_named": "pages whose own template does a job (table, tabs, pagination) that no pattern named in "
                             "the row covers (B3)",
    "grid_outside_named": "pages whose region wrappers use a gap, padding or column class outside the content grid "
                          "the row names, a grey placeholder box, or a bento span (R13)",
}
GEOMETRY_PROPERTIES = {"--x", "--y", "--w", "--h", "--col", "--row"}
COLOUR_LITERAL_RE = re.compile(
    r"(?:\b(?:rgba?|hsla?|oklch|oklab|lab|lch)\((?!\s*var\()|"
    r"(?:fill|stroke|stop-color|color|background(?:-color)?|border(?:-[a-z]+)?-color)\s*[:=]\s*[\"']?#[0-9a-fA-F]{3,8}\b)")
STYLE_ATTR_RE = re.compile(r"""\bstyle\s*=\s*(?:"([^"]*)"|'([^']*)')""")
CLASS_ATTR_RE = re.compile(r"""\bclass\s*=\s*(?:"([^"]*)"|'([^']*)')""")
ARBITRARY_RE = re.compile(r"(?<![\w-])[a-z0-9:!-]*-\[[^\]\s]+\]")
CHART_CALL_RE = re.compile(r"new\s+Chart\s*\(")

# A job a template can be seen doing, and the patterns (by number) whose markup may do it.
JOB_SIGNATURES = {
    "table": (re.compile(r"<table\b|components/(?:data_table|dense_table|workbench_table|table)\.html"),
              {"P03", "P06", "P14", "P18", "P25", "P36"}),
    "tabs": (re.compile(r"role\s*=\s*[\"']tablist[\"']|components/tabs\.html"), {"P02", "P03"}),
    "pagination": (re.compile(r"components/pagination_controls\.html"), {"P05"}),
}
# Content grids of R13: the gap, padding and column classes a region wrapper may carry, per grid.
GRID_BASE = {"gap-2", "gap-4", "md:gap-6", "py-4", "md:py-6", "px-4", "lg:px-6"}
GRID_CLASSES = {
    "G1": GRID_BASE | {"grid-cols-1", "@xl/main:grid-cols-2", "@5xl/main:grid-cols-4"},
    "G2": {"gap-4", "p-4", "auto-rows-min", "md:grid-cols-3"},
    "G3": GRID_BASE,
    "G4": GRID_BASE,
}
FORM_FIELD_GRID = {"grid-cols-2", "grid-cols-3", "col-span-2"}
SPAN_RE = re.compile(r"^(?:[\w@/-]+:)?(?:col|row)-span-")
WRAPPER_TOKEN_RE = re.compile(r"^(?:[\w@/-]+:)?(?:gap(?:-[xy])?|p[xy]?|space-[xy]|grid-cols|col-span|row-span|auto-rows)-")


def style_properties(source):
    """Every property name set in an inline style attribute that holds no template expression."""
    names = set()
    for match in STYLE_ATTR_RE.finditer(source):
        value = match.group(1) if match.group(1) is not None else match.group(2)
        if "{{" in value or "{%" in value:
            value = re.sub(r"{{.*?}}|{%.*?%}", "", value)
        names |= {part.split(":", 1)[0].strip().lower() for part in value.split(";") if ":" in part}
    return names


def charts_drawn(source):
    return max(len(re.findall(r"<canvas\b", source)), len(CHART_CALL_RE.findall(source)))


def class_lists(source):
    """Every class attribute value that holds no template expression, as a list of class tokens."""
    for match in CLASS_ATTR_RE.finditer(source):
        value = match.group(1) if match.group(1) is not None else match.group(2)
        if "{{" in value or "{%" in value:
            value = re.sub(r"{{.*?}}|{%.*?%}", " ", value)
        yield value.split()


def outside_grid(source, grid, patterns):
    """True when a region wrapper (a class list with grid or flex-col) steps outside the named grid."""
    allowed = set(GRID_CLASSES[grid])
    if any(p.split("-")[0] == "P10" for p in patterns):
        allowed |= FORM_FIELD_GRID
    for tokens in class_lists(source):
        if "aspect-video" in tokens and "bg-muted/50" in tokens:
            return True
        wrapper = "grid" in tokens or "flex-col" in tokens
        for token in tokens:
            span = SPAN_RE.match(token)
            if (span or (wrapper and WRAPPER_TOKEN_RE.match(token))) and token not in allowed:
                return True
    return False


def outside_patterns(source, patterns):
    """The jobs the template does that none of the row's patterns covers."""
    named = {p.split("-")[0] for p in patterns}
    return sorted(job for job, (regex, covered) in JOB_SIGNATURES.items()
                  if regex.search(source) and not named & covered)


def tier_checks(design_map, pages, read=None):
    """Each row measured against the checks of its tier that a template can answer today.

    {check: {"count": pages failing, "by_tier": {tier: pages}, "pages": [templates]}}. The source checks read
    the page's own template, so a macro it includes is not followed; they ratchet at today's count.
    """
    verdicts = inhouse_verdicts(design_map)
    read = read or (lambda rel: (ROOT / rel).read_text(encoding="utf-8", errors="replace")
                    if (ROOT / rel).is_file() else None)
    found = {name: [] for name in TIER_CHECKS}
    for rel in sorted(set(design_map["pages"]) & set(pages)):
        entry, tier = design_map["pages"][rel], design_map["pages"][rel].get("tier")
        used = {macro_name(m): verdicts.get(macro_name(m)) for m in pages[rel]["macros"]}
        used = {name: verdict for name, verdict in used.items() if verdict}
        if "Replace" in used.values():
            found["replace_in_use"].append((rel, tier))
        visible = {name: v for name, v in used.items() if name != "accessibility-helpers"}
        beyond = {"A": {"Compose", "Keep"}, "B": {"Keep"}}.get(tier, set())
        if beyond & set(visible.values()):
            found["in_house_beyond_tier"].append((rel, tier))
        source = read(rel)
        if source is None:
            continue
        if tier in ("A", "B") and charts_drawn(source) > 2:
            found["charts_over_two"].append((rel, tier))
        if COLOUR_LITERAL_RE.search(source):
            found["colour_literals"].append((rel, tier))
        extra = style_properties(source) - (GEOMETRY_PROPERTIES if tier and tier.startswith("C") else set())
        if extra:
            found["inline_style"].append((rel, tier))
        if any(ARBITRARY_RE.search(m.group(1) if m.group(1) is not None else m.group(2))
               for m in CLASS_ATTR_RE.finditer(source)):
            found["arbitrary_values"].append((rel, tier))
        if entry.get("patterns") and outside_patterns(source, entry["patterns"]):
            found["pattern_outside_named"].append((rel, tier))
        if entry.get("grid") in GRID_CLASSES and outside_grid(source, entry["grid"], entry.get("patterns") or []):
            found["grid_outside_named"].append((rel, tier))
    out = {}
    for name, hits in found.items():
        by_tier = Counter(tier for _rel, tier in hits)
        out[name] = {"count": len(hits), "by_tier": {t: by_tier[t] for t in TIERS if by_tier[t]},
                     "pages": [rel for rel, _tier in hits]}
    return out


# ------------------------------------------------------------------ the merge check

def check_block(rel, source, design_map):
    """Problems with one changed template, in words a builder can act on. [] when it follows its block."""
    facts = page_facts(rel, source)
    if facts is None:
        return []
    blocks = design_map["blocks"]
    entry = design_map["pages"].get(rel)
    marker = facts["marker"]
    if entry is None:
        if marker is None:
            return [f"{rel}: a new page template must declare its block: add "
                    f"{{# design-block: <block> #}} (one of {', '.join(sorted(blocks))}) "
                    f"and run python scripts/check_design_map.py --write"]
        return [f"{rel}: declares block {marker!r} but is not in docs/design-map.json; "
                f"run python scripts/check_design_map.py --write"]
    block = entry.get("block")
    problems = []
    if block not in blocks:
        return [f"{rel}: the map gives block {block!r}, which is not a block in docs/design-map.json"]
    if marker is None:
        problems.append(f"{rel}: carries no block marker; add {{# design-block: {block} #}}")
    elif marker != block:
        problems.append(f"{rel}: marker says {marker!r} but the map assigns {block!r}; "
                        f"follow the {block} block, or have the designers change the map")
    allowed = set(allowed_macros(design_map, entry))
    for macro in facts["macros"]:
        if macro not in allowed:
            problems.append(f"{rel}: uses {macro}, which the {block} block does not allow "
                            f"(allowed: {', '.join(sorted(allowed))})")
    # The component list is the page as designed. Once the page is rebuilt to it, a macro outside it is a
    # finding on the change; until then the page still holds today's macros and the outside-components
    # ratchet in measure() counts the gap, so a branch that touches many unconverted pages is not refused.
    if entry.get("components") and entry.get("status") in STATUSES[2:]:
        in_row = set(row_macros(design_map, entry))
        for macro in facts["macros"]:
            if macro in allowed and macro not in in_row:
                problems.append(f"{rel}: uses {macro} (component {component_of(macro)!r}), which is not in "
                                f"this page's component list in docs/design-map.json "
                                f"(its components: {', '.join(sorted(entry['components']))})")
    return problems


# ------------------------------------------------------------------ whole tree

def port_map_problems(rel, entry, design_map):
    """The final port map's fields on one row: patterns, states, departures and the content grid."""
    register = design_map.get("pattern_register", {})
    problems = []
    patterns = entry.get("patterns")
    if patterns is None:
        problems.append(f"{rel}: no patterns list from the port map")
    elif not patterns:
        if not (entry.get("pattern_gap") or "").strip():
            problems.append(f"{rel}: names no pattern and does not say which gap leaves it without one")
    for pattern in patterns or []:
        if pattern.split(":")[0] not in register:
            problems.append(f"{rel}: pattern {pattern!r} is not in the pattern register of docs/design-map.json")
    if not entry.get("states"):
        problems.append(f"{rel}: no states from the port map")
    departures = entry.get("departures")
    if departures is None:
        problems.append(f"{rel}: no departures list from the port map")
    for departure in departures or []:
        if departure.get("rule") not in DEPARTURE_RULES or not (departure.get("what") or "").strip():
            problems.append(f"{rel}: departure {departure!r} needs a rule of {', '.join(DEPARTURE_RULES)} and a reason")
    grid = entry.get("grid")
    if grid is not None and grid not in GRIDS:
        problems.append(f"{rel}: grid {grid!r} is not one of {', '.join(GRIDS)}")
    if "grid" not in entry:
        problems.append(f"{rel}: no content grid (one of {', '.join(GRIDS)}, or null for a public page)")
    return problems


def measure(design_map=None, pages=None):
    errors = duplicate_key_problems() if design_map is None else []
    design_map = design_map if design_map is not None else load_map()
    pages = pages if pages is not None else tree_pages()
    blocks = design_map["blocks"]
    entries = design_map["pages"]
    outside, unmarked, outside_components = [], [], []
    personas = design_map.get("personas", {})
    vocabulary = design_map.get("components", {})
    for rel in sorted(set(pages) - set(entries)):
        errors.append(f"{rel}: page template missing from docs/design-map.json")
    for rel in sorted(set(entries) - set(pages)):
        errors.append(f"{rel}: in docs/design-map.json but not a page template in the tree")
    for rel in sorted(set(entries) & set(pages)):
        entry, facts = entries[rel], pages[rel]
        block = entry.get("block")
        if block not in blocks:
            errors.append(f"{rel}: unknown block {block!r}")
            continue
        if entry.get("status") not in STATUSES:
            errors.append(f"{rel}: status {entry.get('status')!r} is not one of {', '.join(STATUSES)}")
        spec = entry.get("spec")
        if spec and not (ROOT / spec).is_file():
            errors.append(f"{rel}: spec {spec} does not exist")
        if entry.get("status") in STATUSES[1:] and not spec:
            errors.append(f"{rel}: status {entry['status']!r} needs a spec")
        if facts["marker"] is None:
            unmarked.append(rel)
        elif facts["marker"] != block:
            errors.append(f"{rel}: marker says {facts['marker']!r} but the map assigns {block!r}")
        allowed = set(allowed_macros(design_map, entry))
        extra = [m for m in facts["macros"] if m not in allowed]
        if extra:
            outside.append({"template": rel, "block": block, "macros": extra})
        if entry.get("persona") not in personas:
            errors.append(f"{rel}: persona {entry.get('persona')!r} is not one of the personas in docs/design-map.json")
        for other in entry.get("also_serves", []):
            if other not in personas:
                errors.append(f"{rel}: also_serves {other!r} is not one of the personas in docs/design-map.json")
        if entry.get("tier") not in TIERS:
            errors.append(f"{rel}: tier {entry.get('tier')!r} is not one of {', '.join(TIERS)}")
        if entry.get("confidence") not in CONFIDENCE:
            errors.append(f"{rel}: confidence {entry.get('confidence')!r} is not one of {', '.join(CONFIDENCE)}")
        for field in ("page_type", "original", "frame"):
            if not entry.get(field):
                errors.append(f"{rel}: no {field.replace('_', ' ')} from the port map")
        if not (entry.get("reason") or "").strip():
            errors.append(f"{rel}: no reason from the port map")
        errors.extend(port_map_problems(rel, entry, design_map))
        listed = entry.get("components") or []
        if not listed:
            errors.append(f"{rel}: lists no components")
        for name in listed:
            if name not in vocabulary:
                errors.append(f"{rel}: component {name!r} is not in the component list of docs/design-map.json")
        in_row = set(row_macros(design_map, entry))
        beyond = [m for m in facts["macros"] if m not in in_row]
        if beyond:
            outside_components.append({"template": rel, "components": sorted({component_of(m) for m in beyond}),
                                       "macros": beyond})
    patterns = json.loads(PATTERNS_PATH.read_text(encoding="utf-8")).get("pilot_patterns", {})
    for name, block in sorted(blocks.items()):
        for key in block.get("patterns", []):
            if key not in patterns:
                errors.append(f"block {name}: pattern {key!r} is not in design/patterns.json")
    return {
        "pages": len(pages),
        "mapped": len(set(entries) & set(pages)),
        "errors": errors,
        "outside_block": outside,
        "outside_block_count": len(outside),
        "unmarked_count": len(unmarked),
        "outside_components": outside_components,
        "outside_components_count": len(outside_components),
        "tiers": dict(sorted(Counter(e.get("tier") for e in entries.values()).items())),
        "tier_checks": tier_checks(design_map, pages),
        "tier_checks_pending": PENDING_TIER_CHECKS,
    }


# ------------------------------------------------------------------ regeneration

def _controls_by_template():
    from component_inventory import build_template_inventory

    counts = {}
    for control in build_template_inventory()["controls"]:
        counts.setdefault(control["template"], Counter())[control["kind"]] += 1
    return {rel: dict(sorted(c.items())) for rel, c in counts.items()}


def regenerate(design_map, pages, controls):
    """The map with derived fields refreshed and designed fields kept. Raises on an undeclared new page."""
    blocks = design_map["blocks"]
    old = design_map["pages"]
    undeclared = sorted(rel for rel in pages if rel not in old and pages[rel]["marker"] not in blocks)
    if undeclared:
        raise SystemExit("new page templates with no valid block marker:\n  " + "\n  ".join(undeclared))
    out = {}
    for rel in sorted(pages):
        facts = pages[rel]
        designed = {k: v for k, v in old.get(rel, {}).items() if k in DESIGNED_FIELDS}
        if not designed:
            designed = {"block": facts["marker"], "basis": "declared", "spec": None, "status": "mapped"}
        entry = dict(designed)
        block = blocks[entry["block"]]
        entry.setdefault("persona", persona_for(design_map, rel, entry["block"]))
        entry.setdefault("components", default_components(design_map, entry, facts))
        entry["reference"] = block["reference"]
        entry["allowed_macros"] = allowed_macros(design_map, entry)
        entry["extends"] = facts["extends"]
        entry["macros"] = facts["macros"]
        entry["controls"] = controls.get(rel, {})
        out[rel] = {k: entry[k] for k in sorted(entry)}
    return {**{k: v for k, v in design_map.items() if k != "pages"}, "pages": out}


def persona_for(design_map, rel, block):
    """The primary persona of a page: the longest matching path rule, else the block's default."""
    rules = design_map.get("persona_rules", {})
    hits = [prefix for prefix in rules if prefix in rel]
    if hits:
        return rules[max(hits, key=len)]
    return design_map["blocks"][block].get("default_persona")


def default_components(design_map, entry, facts):
    """The block's core components plus the components of the page's own macros that its block allows."""
    block = design_map["blocks"][entry["block"]]
    allowed = set(allowed_macros(design_map, entry))
    names = set(block.get("core_components", []))
    names |= {component_of(m) for m in facts["macros"] if m in allowed}
    return sorted(names)


def build_vocabulary(design_map):
    """{component: {source, macros}} over every shared macro any block allows or any page uses."""
    macros = set()
    for block in design_map["blocks"].values():
        macros |= set(block.get("allowed_macros", []))
    for entry in design_map["pages"].values():
        macros |= set(entry.get("macros", [])) | set(entry.get("extra_macros", []))
    out = {}
    for m in sorted(macros):
        name = component_of(m)
        slot = out.setdefault(name, {"source": "shadcn" if Path(m).stem in SHADCN_ALIASES else "app", "macros": []})
        slot["macros"].append(m)
    # A pinned shadcn component the port map names has no in-house macro, but it is still a component.
    for entry in design_map["pages"].values():
        for name in entry.get("components", []):
            out.setdefault(name, design_map.get("components", {}).get(name) or {"source": "shadcn", "macros": []})
    return dict(sorted(out.items()))


def dump(design_map):
    return json.dumps(design_map, indent=1, sort_keys=False, ensure_ascii=False) + "\n"


# ------------------------------------------------------------------ tracker

TIER_NAMES = {"A": "the original verbatim", "B": "composed in the nearest original's frame", "C1": "diagram canvas",
              "C2": "matrix and map", "C3": "timeline", "C4": "board and canvas", "C5": "generated document"}


def tracker_rows(design_map, result):
    """Pages per block and per status, plus pages outside their block."""
    outside = Counter(o["block"] for o in result["outside_block"])
    rows = []
    for name, block in design_map["blocks"].items():
        members = [e for e in design_map["pages"].values() if e["block"] == name]
        by_status = Counter(e["status"] for e in members)
        rows.append({
            "block": name, "title": block["title"], "reference": block["reference"],
            "pages": len(members), "outside": outside.get(name, 0),
            # Status is a ladder: a verified page is also implemented, specified and mapped.
            **{s: sum(by_status[t] for t in STATUSES[i:]) for i, s in enumerate(STATUSES)},
        })
    return rows


def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def render_tracker(design_map, result):
    rows = tracker_rows(design_map, result)
    outside = {o["template"]: o["macros"] for o in result["outside_block"]}
    total = sum(r["pages"] for r in rows)
    block_rows = "\n".join(
        f'<tr data-block="{_esc(r["block"])}"><th scope="row">{_esc(r["title"])}</th>'
        f'<td><a href="{_esc(r["reference"]["shadcn"]["url"])}">{_esc(r["reference"]["shadcn"]["name"])}</a><br>'
        f'<a href="{_esc(r["reference"]["basecoat"]["image"].replace("docs/design/", ""))}">'
        f'{_esc(r["reference"]["basecoat"]["name"])}</a></td>'
        f'<td class="n"><button type="button" class="show-block" data-block="{_esc(r["block"])}" '
        f'aria-label="Show the {r["pages"]} {_esc(r["title"])} pages">{r["pages"]}</button></td>'
        f'<td class="n">{r["mapped"]}</td><td class="n">{r["specified"]}</td>'
        f'<td class="n">{r["implemented"]}</td><td class="n">{r["verified"]}</td>'
        f'<td class="n">{r["outside"]}</td></tr>'
        for r in rows
    )
    startup = [(rel, e) for rel, e in design_map["pages"].items() if e.get("spec")]
    startup.sort(key=lambda item: design_map["startup_path"].index(item[0])
                 if item[0] in design_map["startup_path"] else 999)
    cards = []
    for rel, e in startup:
        spec = e["spec"].replace("docs/design/", "")
        slug = Path(e["spec"]).stem
        cards.append(
            f'<article class="card" data-template="{_esc(rel)}"><h3>{_esc(design_map["page_names"].get(rel, rel))}</h3>'
            f'<p class="muted">{_esc(rel)} &middot; {_esc(design_map["blocks"][e["block"]]["title"])} &middot; {_esc(e["status"])}</p>'
            f'<div class="pair"><figure><img src="specs/img/{_esc(slug)}-current.webp" alt="The page today">'
            f'<figcaption>Today</figcaption></figure>'
            f'<figure><img src="{_esc(e["reference"]["basecoat"]["image"].replace("docs/design/", ""))}" '
            f'alt="The reference it follows"><figcaption>Reference: {_esc(e["reference"]["basecoat"]["name"])}</figcaption></figure></div>'
            f'<p><a class="spec" href="{_esc(spec)}">Open the redesign spec</a></p></article>'
        )
    page_rows = "\n".join(
        f'<tr data-block="{_esc(e["block"])}" data-persona="{_esc(e["persona"])}" data-tier="{_esc(e["tier"])}" '
        f'data-confidence="{_esc(e["confidence"])}"><td>{_esc(rel)}</td>'
        f'<td>{_esc(design_map["blocks"][e["block"]]["title"])}</td>'
        f'<td>{_esc(design_map["personas"][e["persona"]])}</td>'
        f'<td>{_esc(", ".join(e["components"]))}</td><td>{_esc(e["tier"])}</td>'
        f'<td>{_esc(e["page_type"])}{" (corrected)" if e["type_changed"] else ""}<br>'
        f'<span class="muted">{_esc(e["reason"])}</span></td><td>{_esc(e["confidence"])}</td>'
        f'<td>{_esc(", ".join(e.get("patterns") or []) or e.get("pattern_gap") or "None")}</td>'
        f'<td>{_esc(", ".join(d["rule"] for d in e.get("departures") or []) or "None")}</td>'
        f'<td>{_esc(e["status"])}</td>'
        f'<td>{_esc(", ".join(outside[rel])) if rel in outside else "None: follows its block"}</td>'
        f'<td>{(chr(60) + "a href=" + chr(34) + _esc(e["spec"].replace("docs/design/", "")) + chr(34) + ">Spec</a>") if e.get("spec") else "Not written yet"}</td></tr>'
        for rel, e in design_map["pages"].items()
    )
    options = "".join(f'<option value="{_esc(r["block"])}">{_esc(r["title"])}</option>' for r in rows)
    persona_options = "".join(
        f'<option value="{_esc(key)}">{_esc(name)}</option>' for key, name in design_map["personas"].items()
        if any(e["persona"] == key for e in design_map["pages"].values()))
    tier_counts = ", ".join(f"{t} {n}" for t, n in result["tiers"].items())
    tier_options = "".join(f'<option value="{_esc(t)}">Tier {_esc(t)}: {_esc(TIER_NAMES[t])} ({n})</option>'
                           for t, n in result["tiers"].items())
    check_rows = "\n".join(
        f'<tr data-check="{_esc(name)}"><th scope="row">{_esc(TIER_CHECKS[name])}</th>'
        f'<td class="n">{found["count"]}</td>'
        f'<td>{_esc(", ".join(f"{t} {n}" for t, n in found["by_tier"].items()) or "None")}</td></tr>'
        for name, found in result["tier_checks"].items())
    pending = "".join(f"<li><strong>{_esc(k)}</strong>: needs {_esc(v)}</li>"
                      for k, v in result["tier_checks_pending"].items())
    return TRACKER_TEMPLATE.format(
        tier_counts=_esc(tier_counts), tier_options=tier_options, check_rows=check_rows, pending=pending,
        total=total, block_rows=block_rows, cards="\n".join(cards), page_rows=page_rows, options=options, persona_options=persona_options,
        outside=result["outside_block_count"], outside_components=result["outside_components_count"], unmarked=result["unmarked_count"],
        specified=sum(r["specified"] for r in rows),
    )


TRACKER_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Design map tracker</title>
<!-- Generated by scripts/check_design_map.py --write from docs/design-map.json. Do not edit by hand. -->
<link rel="stylesheet" href="../../app/static/css/shadcn_tokens.css">
<script>if (window.matchMedia("(prefers-color-scheme: dark)").matches) document.documentElement.classList.add("dark");</script>
<style>
:root {{ color-scheme: light dark; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; font:14px/1.5 Inter, system-ui, sans-serif; background:hsl(var(--background)); color:hsl(var(--foreground)); }}
main {{ max-width:1200px; margin:0 auto; padding:24px; }}
h1 {{ font-size:24px; margin:0 0 4px; }} h2 {{ font-size:18px; margin:24px 0 8px; }} h3 {{ font-size:15px; margin:0; }}
.muted {{ color:hsl(var(--muted-foreground)); margin:4px 0; }}
[role=tablist] {{ display:flex; gap:4px; border-bottom:1px solid hsl(var(--border)); margin:16px 0; flex-wrap:wrap; }}
[role=tab] {{ font:inherit; background:none; border:0; border-bottom:2px solid transparent; padding:8px 12px; color:hsl(var(--muted-foreground)); cursor:pointer; }}
[role=tab][aria-selected=true] {{ color:hsl(var(--foreground)); border-color:hsl(var(--foreground)); font-weight:600; }}
.scroll {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; }}
th, td {{ border-bottom:1px solid hsl(var(--border)); padding:6px 8px; text-align:left; vertical-align:top; }}
thead th {{ font-size:12px; color:hsl(var(--muted-foreground)); font-weight:600; }}
td.n {{ text-align:right; font-variant-numeric:tabular-nums; }}
.show-block {{ font:inherit; background:hsl(var(--muted)); color:hsl(var(--foreground)); border:1px solid hsl(var(--border)); border-radius:6px; padding:2px 10px; cursor:pointer; }}
a {{ color:inherit; }}
.cards {{ display:grid; gap:16px; }}
.card {{ border:1px solid hsl(var(--border)); border-radius:8px; padding:16px; }}
.pair {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
@media (max-width:640px) {{ .pair {{ grid-template-columns:1fr; }} main {{ padding:12px; }} }}
figure {{ margin:8px 0; }} img {{ width:100%; border:1px solid hsl(var(--border)); border-radius:6px; }}
figcaption {{ font-size:12px; color:hsl(var(--muted-foreground)); }}
.filters {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:8px; }}
.filters label {{ max-width:100%; }} .filters select {{ max-width:100%; }}
select, input {{ font:inherit; padding:6px 8px; border:1px solid hsl(var(--border)); border-radius:6px; background:hsl(var(--background)); color:hsl(var(--foreground)); }}
.summary {{ display:flex; gap:24px; flex-wrap:wrap; }} .summary div strong {{ display:block; font-size:22px; }}
</style>
</head>
<body>
<main>
<h1>Design map</h1>
<p class="muted">Which designed block every page follows, how far each page has got, and which pages still use something their block does not allow.</p>
<div class="summary">
<div><strong id="total-pages">{total}</strong>pages mapped to a block</div>
<div><strong id="specified-pages">{specified}</strong>with a redesign spec</div>
<div><strong id="outside-pages">{outside}</strong>use a macro their block does not allow</div>
<div><strong id="unmarked-pages">{unmarked}</strong>do not yet name their block in the template</div>
<div><strong id="outside-components">{outside_components}</strong>use a component their row does not list</div>
</div>
<p class="muted">Pages by tier: <span id="tier-counts">{tier_counts}</span></p>
<div role="tablist" aria-label="Views">
<button type="button" role="tab" id="tab-blocks" aria-controls="panel-blocks" aria-selected="true">Blocks</button>
<button type="button" role="tab" id="tab-startup" aria-controls="panel-startup" aria-selected="false">Startup path</button>
<button type="button" role="tab" id="tab-pages" aria-controls="panel-pages" aria-selected="false">All pages</button>
</div>
<section role="tabpanel" id="panel-blocks" aria-labelledby="tab-blocks">
<div class="scroll"><table>
<thead><tr><th scope="col">Block</th><th scope="col">Follows</th><th scope="col">Pages</th><th scope="col">Mapped</th><th scope="col">Specified</th><th scope="col">Implemented</th><th scope="col">Verified</th><th scope="col">Outside block</th></tr></thead>
<tbody>
{block_rows}
</tbody></table></div>
<h2>Checks against each page's tier</h2>
<p class="muted">What the gate can measure from the templates today. Each count may only fall.</p>
<div class="scroll"><table id="tier-checks">
<thead><tr><th scope="col">Check</th><th scope="col">Pages failing</th><th scope="col">Tiers</th></tr></thead>
<tbody>
{check_rows}
</tbody></table></div>
<p class="muted">Not measured yet, because each needs a rendered original or a browser:</p>
<ul id="tier-pending">{pending}</ul>
<p class="muted">Mapped: the page has a block. Specified: it has a redesign spec. Implemented: it has been rebuilt to the spec. Verified: someone walked it beside the reference. Each stage counts the pages that have reached it or gone further.</p>
</section>
<section role="tabpanel" id="panel-startup" aria-labelledby="tab-startup" hidden>
<p class="muted">The pages a new customer meets first, each with the page today beside the reference it will follow.</p>
<div class="cards">
{cards}
</div>
</section>
<section role="tabpanel" id="panel-pages" aria-labelledby="tab-pages" hidden>
<div class="filters">
<label>Block <select id="block-filter"><option value="">Every block</option>{options}</select></label>
<label>Persona <select id="persona-filter"><option value="">Every persona</option>{persona_options}</select></label>
<label>Tier <select id="tier-filter"><option value="">Every tier</option>{tier_options}</select></label>
<label>Confidence <select id="confidence-filter"><option value="">Any confidence</option><option value="high">High</option><option value="low">Low</option></select></label>
<label>Search <input id="page-search" type="search" placeholder="Template path"></label>
<span class="muted" id="page-count" aria-live="polite"></span>
</div>
<div class="scroll"><table id="pages-table">
<thead><tr><th scope="col">Template</th><th scope="col">Block</th><th scope="col">Persona</th><th scope="col">Components</th><th scope="col">Tier</th><th scope="col">Page type and why</th><th scope="col">Confidence</th><th scope="col">Patterns</th><th scope="col">Departures</th><th scope="col">Status</th><th scope="col">Macros outside its block</th><th scope="col">Spec</th></tr></thead>
<tbody>
{page_rows}
</tbody></table></div>
</section>
</main>
<script>
(function () {{
  var tabs = Array.prototype.slice.call(document.querySelectorAll('[role=tab]'));
  function select(tab) {{
    tabs.forEach(function (t) {{
      var on = t === tab;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
    }});
  }}
  tabs.forEach(function (t) {{ t.addEventListener('click', function () {{ select(t); }}); }});
  var filter = document.getElementById('block-filter');
  var persona = document.getElementById('persona-filter');
  var tier = document.getElementById('tier-filter');
  var confidence = document.getElementById('confidence-filter');
  var search = document.getElementById('page-search');
  var rows = Array.prototype.slice.call(document.querySelectorAll('#pages-table tbody tr'));
  function apply() {{
    var block = filter.value, who = persona.value, text = search.value.toLowerCase(), shown = 0;
    var level = tier.value, sure = confidence.value;
    rows.forEach(function (r) {{
      var on = (!block || r.getAttribute('data-block') === block) && (!who || r.getAttribute('data-persona') === who) && (!level || r.getAttribute('data-tier') === level) && (!sure || r.getAttribute('data-confidence') === sure) && r.cells[0].textContent.toLowerCase().indexOf(text) !== -1;
      r.hidden = !on; if (on) shown += 1;
    }});
    document.getElementById('page-count').textContent = shown + ' of ' + rows.length + ' pages';
  }}
  filter.addEventListener('change', apply);
  persona.addEventListener('change', apply);
  tier.addEventListener('change', apply);
  confidence.addEventListener('change', apply);
  search.addEventListener('input', apply);
  Array.prototype.slice.call(document.querySelectorAll('.show-block')).forEach(function (b) {{
    b.addEventListener('click', function () {{
      filter.value = b.getAttribute('data-block'); persona.value = ''; tier.value = ''; confidence.value = ''; search.value = ''; apply(); select(document.getElementById('tab-pages'));
    }});
  }});
  apply();
}})();
</script>
</body>
</html>
"""


# ------------------------------------------------------------------ CLI

def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def _check_sources(sources, design_map):
    problems = []
    for rel, source in sources:
        problems += check_block(rel, source, design_map)
    return problems


def _is_template(path):
    return path.startswith("app/") and "/templates/" in path and path.endswith((".html", ".jinja"))


def changed_problems(design_map):
    """check_block on every page template this branch changes (committed, uncommitted or new)."""
    from check_smoke_coverage_on_change import _base_ref, _changed_files

    files = sorted(p for p in _changed_files(_base_ref()) if _is_template(p) and (ROOT / p).is_file())
    sources = [(p, (ROOT / p).read_text(encoding="utf-8", errors="replace")) for p in files]
    return _check_sources(sources, design_map)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--files", nargs="+")
    ap.add_argument("--base")
    ap.add_argument("--pr", type=int)
    ap.add_argument("--changed", action="store_true")
    ap.add_argument("--remote", default="anioko")
    a = ap.parse_args(argv)

    if a.write:
        design_map = regenerate(load_map(), tree_pages(), _controls_by_template())
        MAP_PATH.write_text(dump(design_map), encoding="utf-8")
        result = measure(design_map)
        TRACKER_PATH.write_text(render_tracker(design_map, result), encoding="utf-8")
        print(f"wrote {MAP_PATH.relative_to(ROOT)} ({result['mapped']} pages) and {TRACKER_PATH.relative_to(ROOT)}")
        return 0

    if a.files or a.base or a.pr:
        if a.pr:
            info = json.loads(subprocess.run(
                ["gh", "pr", "view", str(a.pr), "--json", "headRefOid,files,title"],
                cwd=ROOT, capture_output=True, text=True, check=True).stdout)
            head = info["headRefOid"]
            _git("fetch", "-q", a.remote, f"pull/{a.pr}/head")
            files = [f["path"] for f in info["files"] if _is_template(f["path"])]
            design_map = json.loads(_git("show", f"{head}:docs/design-map.json")) \
                if "docs/design-map.json" in _git("ls-tree", "-r", "--name-only", head, "docs/") else load_map()
            sources = []
            for path in files:
                try:
                    sources.append((path, _git("show", f"{head}:{path}")))
                except subprocess.CalledProcessError:
                    continue  # deleted by the pull request
            print(f"PR {a.pr} ({info['title']}) head {head[:8]}: {len(files)} template(s) changed")
        else:
            files = a.files or [p for p in _git("diff", "--name-only", f"{a.base}...HEAD").split() if _is_template(p)]
            design_map = load_map()
            sources = [(p, (ROOT / p).read_text(encoding="utf-8", errors="replace"))
                       for p in files if (ROOT / p).is_file()]
        problems = _check_sources(sources, design_map)
        for p in problems:
            print("  -", p)
        print(f"{'BLOCKS' if problems else 'PASSES'}: {len(problems)} problem(s) in {len(sources)} template(s)")
        return 1 if problems else 0

    result = measure()
    if a.changed:
        result["changed_problems"] = changed_problems(load_map())
        result["errors"] = result["errors"] + result["changed_problems"]
    if a.json:
        print(json.dumps(result, indent=1))
    else:
        for e in result["errors"]:
            print("ERROR", e)
        for o in result["outside_block"]:
            print(f"outside {o['block']}: {o['template']} uses {', '.join(o['macros'])}")
        print(f"{result['mapped']} of {result['pages']} page templates mapped; "
              f"{result['outside_block_count']} outside their block; {result['unmarked_count']} without a marker; "
              f"{len(result['errors'])} error(s)")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
