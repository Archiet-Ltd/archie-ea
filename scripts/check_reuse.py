#!/usr/bin/env python
"""A second implementation of something the tree already has, caught early.

Two macros can share a name across two template files with nothing at either
call site to tell them apart: a Jinja `{% from 'x.html' import name %}`
binds to whichever file the import names, so identical call text is correct
against one definition and wrong against the other, with no error until the
mismatched call actually executes. The same blind spot recurs for a
diagram-drawing library: a second `<script src="vendor/d3.min.js">` loads a
second copy of a library the product already ships a canonical renderer
for, invisible to review unless the reviewer already knows that file by
name. `docs/reuse-register.yml` is the list of canonical answers a role
should find before building a second one; this script is the ratchet that
keeps each defect class below from growing once it is registered.

Three rules:

RG-1 macro-names
    A Jinja macro name defined in more than one template file. One finding
    per NAME that has two or more distinct defining files (not one per
    file): a name in three files is still a single finding, because the fix
    is one decision -- pick the canonical file -- not three. The two exact
    names the page-shell per-page protocol requires every page to define,
    `_ps_actions` and `_ps_sub`, are excluded by name, nothing more; any
    other name -- including another leading underscore -- is counted.

RG-1b macro-definitions
    A companion count over the same map RG-1 builds: the number of macro
    DEFINITIONS that belong to an already-duplicated name. RG-1 alone cannot
    see a name already in two files gaining a third, fourth or fourteenth
    copy, because its count is of names, not definitions; this rule is that
    missing count. A marker is honoured here only for a name with a
    matching register concept -- the canonical for an unregistered name is
    only ever a guess, so a marker anchored to it would go live or fall
    dormant as files are added; every definition of an unregistered name
    therefore counts, always. For a registered name the escape hatch
    applies PER DEFINITION, unlike RG-1's all-or-nothing suppression: a
    marked definition does not count, but the canonical and every other,
    unmarked definition still do -- and a marker placed where it cannot
    take effect (the canonical, or any definition of an unregistered name)
    is reported in that finding as having no effect, not silently ignored.

RG-2 diagram-libraries
    A template or script that carries a text reference to a diagram-drawing
    library (joint/d3/dagre/mermaid/cytoscape/drawflow, any build or
    suffixed variant, anywhere in the tree, not only under `vendor/`)
    outside the pages that actually load the canonical ArchiMate renderer.
    One finding per (file, base library name) pair. Counts a page-level
    reference regardless of the context it appears in -- a documentation
    link, a dead comment and a live `<script>` tag are all the same
    finding, since deciding whether to delete, keep or register it is a
    human call this rule cannot make from the text alone. It does not, and
    cannot, see a JavaScript file that draws with a library tag already
    present elsewhere on the page -- a real, disclosed gap, not a claim
    this rule catches every second drawing engine. A page actually loads
    the canonical renderer -- and so may still reference `joint` and
    `dagre`, since the canonical renderer is built on them -- only via a
    real `src`/`href`/`filename` attribute (no word or hyphen character
    before it) or an ES-module `import` of the file (static, bare, dynamic,
    or spanning several lines), tested with HTML, Jinja, block and line
    comments all blanked first, so a comment cannot forge the allowance.
    That blanking is for the allowance test ONLY: the library match itself
    always reads the unblanked text, since a library named in a comment is
    still a file that draws with it.

Both patterns, scopes and exclusions below are the same ones recorded in
``docs/reuse-register.yml``'s ``rules:`` block for a person to read -- kept
in step by hand, since those fields mix glob-like paths with plain English
qualifiers ("not app/static/") written for a reader, not a parser. The
register's structured ``concepts:`` list (canonical path and one-line use)
IS read at run time, to fill in the "Already exists" half of a finding's
message when a duplicated name or a referenced library resolves to a
registered concept; a name or library with no matching concept says so
plainly instead of inventing one.

Escape hatch: a line carrying ``reuse-ok: <concept-id> <reason>`` -- an
identifier-shaped concept id, then a reason of two or more whitespace-
separated tokens each containing at least two letters -- excuses that one
definition or reference, for a name or library WITH a matching register
concept only; a marker anywhere on an unregistered one is never honoured
(see RG-1b above for why). For RG-1, a NAME is suppressed only when EVERY
non-canonical definition carries a valid marker; a marker on the canonical
alone changes nothing. For RG-1b the marker works per DEFINITION on a
registered name. ``--summary`` prints how many valid markers exist in the
tree today, so a marker cannot silently accumulate unread.

Usage:
    python scripts/check_reuse.py --rule RG-1
    python scripts/check_reuse.py --rule RG-1 --count [--root <tree>]
    python scripts/check_reuse.py --rule RG-1b --count [--root <tree>]
    python scripts/check_reuse.py --rule RG-2 --count [--root <tree>]
    python scripts/check_reuse.py --summary [--root <tree>]

Proven-against: a synthetic tree with a second ``{% macro empty_state( %}``
in a second template -- red at 1 naming both files, green at 0 with the
second file removed; a synthetic template loading ``vendor/d3.min.js`` with
no real renderer reference anywhere in the tree -- red at 1, green at 0 once
a genuine `src="archimate/composer_renderer.js"` reference is added; a
synthetic tree gaining a third copy of an already-duplicated macro name --
RG-1's count unchanged, RG-1b's count raised by one; the same third copy of
a REGISTERED name carrying a valid marker -- RG-1b unchanged, because a
marked definition of a registered name does not count; and the same marker
on a copy of an UNREGISTERED name -- RG-1b still counts it, and says the
marker has no effect.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is pinned in requirements.txt
    yaml = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The one marker name the register defines for every rule (docs/reuse-register.yml's
# top-level `marker:` field), not a per-rule name -- a builder only has to
# remember one escape-hatch spelling. MARKER just detects the marker is present
# at all (for indexing); VALID_MARKER additionally requires the shape the
# register specifies, so a malformed marker never suppresses a finding.
#
# The reason must be two or more whitespace-separated tokens, each containing
# at least two letters -- "reuse-ok: q 1 2" and "reuse-ok: zzz ... ,,," do not
# qualify (no token has two letters); "reuse-ok: id a real reason" does.
MARKER = re.compile(r"reuse-ok:")
_WORD_TOKEN = r"[^\s{}%#]*(?:[A-Za-z][^\s{}%#]*){2,}"
VALID_MARKER = re.compile(
    r"reuse-ok:\s*([A-Za-z][\w-]*)\s+(%s(?:\s+%s)+)" % (_WORD_TOKEN, _WORD_TOKEN)
)

RULE_NAMES = {"RG-1": "macro-names", "RG-1b": "macro-definitions", "RG-2": "diagram-libraries"}
BASELINE_KEYS = {
    "RG-1": "reuse_macro_names",
    "RG-1b": "reuse_macro_definitions",
    "RG-2": "reuse_diagram_libraries",
}

# ---------------------------------------------------------------- RG-1 / RG-1b: macros

MACRO_PATTERN = re.compile(r"\{%-?\s*macro\s+([A-Za-z_]\w*)\s*\(")
MACRO_SCOPE_EXTS = (".html", ".j2", ".jinja")
# "node_modules, vendor, bundles" and the generated-code templates, per the
# register's RG-1 `excluded` list.
MACRO_EXCLUDED = ("node_modules/", "vendor/", "bundles/", "app/modules/solutions_product/templates/")
# The page-shell per-page protocol requires exactly these two names, one
# intentional definition per page. Exact names only, not a prefix: a prefix
# is a blanket bypass (a duplicate renamed `_ps_anything` would vanish from
# both rules), and the exemption this set was measured to justify only ever
# covered these two. Add a new page-shell protocol name here -- and nowhere
# else -- as its own line, so a reviewer sees it in the diff.
PAGE_SHELL_PROTOCOL_NAMES = frozenset({"_ps_actions", "_ps_sub"})

# ---------------------------------------------------------------- RG-2: diagram-libraries

# A library name, optionally suffixed (`d3-sankey`, `dagre-d3`), as a `.js`
# file referenced anywhere in the tree, not only under `vendor/`. The
# boundary before the name (start of string, a quote, or a path separator)
# stops "id3.min.js" matching mid-word; the boundary after it (nothing, or a
# `-`/`.`) stops "jointly_shared.js" and "mermaid_docs_page.js" matching --
# a real suffixed build always joins with `-` or `.`, never a bare letter.
# Neither boundary requires `vendor/`, so a copy vendored elsewhere, or a
# documentation link, is still seen: a reference by name counts regardless
# of the context it appears in (see the module docstring).
DIAGRAM_LIB_PATTERN = re.compile(
    r"""(?:^|["'/])(joint|d3|dagre|mermaid|cytoscape|drawflow)(?:[-.][\w.\-]*)?(?:\.min)?\.js\b"""
)
DIAGRAM_EXCLUDED = ("vendor/", "bundles/", "solutions_product")

# The two halves of the composer-allowance test. Both are evaluated against
# text with comments BLANKED (see _grants_composer_allowance) -- that
# blanking is for this test only, never for DIAGRAM_LIB_PATTERN above.
#
# 1. A real attribute load: src=/href=/filename=, bounded so no word or
#    hyphen character precedes it (rules out `data-filename=`, `data-src=`).
COMPOSER_LOAD_PATTERN = re.compile(
    r"(?<![\w-])(?:src|href|filename)\s*[:=]\s*[\"'][^\"']*archimate/composer_renderer\.js[\"']"
)
# 2. An ES-module import: static (`import X from '...'`), bare (`import
#    '...'`) or dynamic (`import('...')`), on one line or spanning several --
#    `[^;]{0,300}?` crosses newlines (a Python character class excludes only
#    the characters named) but stops at a statement-ending `;` and is capped
#    at a few hundred characters, so a normal multi-line named import still
#    matches while a stray quoted path far below an unrelated `import` does
#    not.
COMPOSER_IMPORT_PATTERN = re.compile(
    r"\bimport\b[^;]{0,300}?[\"'][^\"']*archimate/composer_renderer\.js[\"']"
)
COMPOSER_ALLOWED_LIBS = {"joint", "dagre"}


def _blank_comments(text: str) -> str:
    """Blank HTML (``<!-- -->``), Jinja (``{# #}``), block (``/* */``) and
    line (``//``) comment bodies, preserving newlines (and so line numbers)
    -- the same character-scan technique as check_broken_surfaces.py's
    _blank_comments, extended with the HTML form that checker does not need.

    A ``//`` immediately preceded by ``:`` or ``/`` is not treated as a
    comment, so a ``https://`` inside a string does not blank the rest of
    its line. Because this function decides only whether the composer
    ALLOWANCE is granted, never what the library match sees, a `//` this
    guard fails to blank can only cost a page its allowance (a false
    finding), never grant one it should not have (a false allowance) -- the
    safer direction to be wrong in.

    Used only to decide the composer allowance; the library match itself
    always reads the original, unblanked text.
    """
    out = list(text)

    def blank(a, b):
        for k in range(a, min(b, len(out))):
            if out[k] != "\n":
                out[k] = " "

    spans = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("<!--", i):
            j = text.find("-->", i + 4)
            j = n if j == -1 else j + 3
            spans.append((i, j))
            i = j
        elif text.startswith("{#", i):
            j = text.find("#}", i + 2)
            j = n if j == -1 else j + 2
            spans.append((i, j))
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            spans.append((i, j))
            i = j
        elif text.startswith("//", i) and (i == 0 or text[i - 1] not in ":/"):
            j = text.find("\n", i)
            j = n if j == -1 else j
            spans.append((i, j))
            i = j
        else:
            i += 1
    for a, b in spans:
        blank(a, b)
    return "".join(out)


def _grants_composer_allowance(text: str) -> bool:
    """Whether *text* (a whole file's contents) really loads the canonical
    renderer -- comments blanked first, so a comment cannot forge this."""
    blanked = _blank_comments(text)
    return bool(COMPOSER_LOAD_PATTERN.search(blanked)) or bool(COMPOSER_IMPORT_PATTERN.search(blanked))


def _rel(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def _walk_files(root: str, under: str, exts: tuple[str, ...]):
    base = os.path.join(root, *under.split("/"))
    if not os.path.isdir(base):
        return
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in ("__pycache__", ".git"))
        for filename in sorted(filenames):
            if filename.endswith(exts):
                path = os.path.join(dirpath, filename)
                yield path, _rel(root, path)


def _excluded(rel: str, substrings: tuple[str, ...]) -> bool:
    return any(s in rel for s in substrings)


def _has_valid_marker(line: str) -> bool:
    return bool(VALID_MARKER.search(line))


# ---------------------------------------------------------------- register lookups


def _load_register(root: str) -> dict:
    path = os.path.join(root, "docs", "reuse-register.yml")
    if yaml is None or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return {}
    return data or {}


def _concepts_for_rule(register: dict, rule_id: str) -> list[dict]:
    return [c for c in (register.get("concepts") or []) if rule_id in (c.get("rules") or [])]


def _concept_for_macro(register: dict, name: str) -> dict | None:
    """A concept whose canonical.use names this macro as a call, e.g. 'empty_state('."""
    for concept in _concepts_for_rule(register, "RG-1"):
        use = ((concept.get("canonical") or {}).get("use")) or ""
        if re.search(r"\b%s\s*\(" % re.escape(name), use):
            return concept
    return None


def _concept_for_diagram(register: dict) -> dict | None:
    concepts = _concepts_for_rule(register, "RG-2")
    return concepts[0] if concepts else None


def _load_baseline(root: str) -> dict:
    path = os.path.join(root, "verification_baseline.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data.get("ratchets", {})


# ---------------------------------------------------------------- message format


def _format_finding(rule: str, path: str, line: int, concept_id: str, what: str,
                     canonical_path: str, canonical_use: str) -> str:
    rule_name = RULE_NAMES[rule]
    return (
        "%s:%d [reuse:%s/%s] %s.\n"
        "  Already exists: %s -- %s\n"
        "  Do one of: (1) use the existing one; (2) if this is a genuinely different job, "
        "add it under allowed_specialisations for\n"
        "  '%s' in docs/reuse-register.yml with a reason and the role that accepted it; "
        "(3) append 'reuse-ok: %s <reason>' to this line."
        % (path, line, rule_name, concept_id, what, canonical_path, canonical_use,
           concept_id, concept_id)
    )


def _unregistered_finding(rule: str, subject: str, flagged: str, flagged_line: int,
                           files: list[str], locations: dict[str, int]) -> str:
    """A finding for a name/reference with no matching register concept,
    reporting the definition it is actually about -- not always the
    alphabetically-first file, so a removed definition becomes visible in
    the output and two findings for the same name are never identical text.

    No concept id to cite, so this does not invent one, does not suggest a
    marker naming a concept that does not exist (the register-integrity
    check would reject it), and does not claim an order among the OTHER
    files listed -- they are sorted alphabetically for a stable report,
    nothing more.
    """
    other_files = [f for f in files if f != flagged]
    also = ""
    if other_files:
        also = "; also at %s" % ", ".join("%s:%d" % (f, locations[f]) for f in other_files)
    return (
        "%s:%d [reuse:%s] %s is defined in %d files with no matching entry in "
        "docs/reuse-register.yml%s.\n"
        "  Add a concept for it in docs/reuse-register.yml naming the canonical file "
        "and its use, before choosing which definition to keep or appending a "
        "reuse-ok marker."
        % (flagged, flagged_line, RULE_NAMES[rule], subject, len(files), also)
    )


# ---------------------------------------------------------------- RG-1 / RG-1b macro index


def _macro_index(root: str) -> dict[str, dict[str, tuple[int, str]]]:
    """{macro name: {file: (line, the line's text)}}, all definitions, unfiltered by marker.

    RG-1 and RG-1b both read this same map -- RG-1b's whole point is that it
    must see every definition RG-1's name-level count cannot.
    """
    by_name: dict[str, dict[str, tuple[int, str]]] = {}
    for path, rel in _walk_files(root, "app", MACRO_SCOPE_EXTS):
        if _excluded(rel, MACRO_EXCLUDED):
            continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, start=1):
            match = MACRO_PATTERN.search(line)
            if not match:
                continue
            name = match.group(1)
            if name in PAGE_SHELL_PROTOCOL_NAMES:
                continue
            # A file defining the same macro name twice is a redefinition
            # (ruff F811's job); only the first definition per file counts
            # toward how many DISTINCT files carry this name.
            by_name.setdefault(name, {}).setdefault(rel, (lineno, line))
    return by_name


def _canonical_path(locations: dict[str, tuple[int, str]], concept: dict | None) -> str:
    """The file RG-1/RG-1b treat as canonical for a duplicated name: the
    register's canonical.paths entry that is actually one of the duplicate's
    files, if any, else the alphabetically first -- shared by both rules so
    they never disagree on which file is "the" one. For a name with no
    matching concept this is only ever a guess (see RG-1b's marker rule)."""
    files = sorted(locations)
    if concept:
        for candidate in (concept.get("canonical") or {}).get("paths") or []:
            if candidate in locations:
                return candidate
    return files[0]


def scan_rg1(root: str) -> list[str]:
    register = _load_register(root)
    by_name = _macro_index(root)

    findings = []
    for name in sorted(by_name):
        locations = by_name[name]
        if len(locations) < 2:
            continue
        concept = _concept_for_macro(register, name)
        canonical_path = _canonical_path(locations, concept)
        finding = _rg1_finding(name, locations, concept, canonical_path)
        if finding is not None:
            findings.append(finding)
    return findings


def _rg1_finding(name: str, locations: dict[str, tuple[int, str]], concept: dict | None,
                  canonical_path: str) -> str | None:
    files = sorted(locations)

    # The finding is suppressed only when EVERY non-canonical definition
    # carries a valid marker. A marker on the canonical definition is not
    # examined here at all -- it was never the file this rule flags.
    non_canonical = [f for f in files if f != canonical_path]
    unmarked = [f for f in non_canonical if not _has_valid_marker(locations[f][1])]
    if not unmarked:
        return None

    flagged = unmarked[0]
    flagged_line = locations[flagged][0]
    other_files = [f for f in files if f != flagged]

    if concept is None:
        return _unregistered_finding(
            "RG-1", "macro '%s'" % name, flagged, flagged_line, files,
            {f: loc[0] for f, loc in locations.items()},
        )

    also_defined_in = ", ".join("%s:%d" % (f, locations[f][0]) for f in other_files)
    concept_id = concept["id"]
    canonical_use = (concept.get("canonical") or {}).get("use", canonical_path)
    what = "macro '%s' is also defined in %s" % (name, also_defined_in)
    return _format_finding("RG-1", flagged, flagged_line, concept_id, what,
                            canonical_path, canonical_use)


def scan_rg1b(root: str) -> list[str]:
    """Every definition behind a duplicated name, except a non-canonical one
    of a REGISTERED name that carries a valid marker -- the marker applies
    per definition there, not per name: one accepted copy removes exactly
    one from this count, however many siblings (marked or not) the same
    name still has. A name with no matching concept has no stable canonical
    to anchor a marker to, so every one of its definitions counts, always."""
    register = _load_register(root)
    by_name = _macro_index(root)

    findings = []
    for name in sorted(by_name):
        locations = by_name[name]
        if len(locations) < 2:
            continue
        concept = _concept_for_macro(register, name)
        canonical_path = _canonical_path(locations, concept)
        files = sorted(locations)
        for f in files:
            lineno, line_text = locations[f]
            if concept is not None and f != canonical_path and _has_valid_marker(line_text):
                continue
            findings.append(
                _rg1b_finding(name, f, lineno, line_text, files, locations, concept, canonical_path)
            )
    return findings


def _rg1b_finding(name: str, flagged: str, flagged_line: int, flagged_text: str, files: list[str],
                   locations: dict[str, tuple[int, str]], concept: dict | None,
                   canonical_path: str) -> str:
    is_canonical = flagged == canonical_path
    other_files = [f for f in files if f != flagged]
    also_at = ", ".join("%s:%d" % (f, locations[f][0]) for f in other_files)

    # A marker has no effect here either because this name has no matching
    # concept (RG-1b never honours a marker on one), or because this IS the
    # canonical definition (always counted, marker or not). Report it rather
    # than silently ignore it -- someone wrote that marker meaning something.
    dead_marker_note = ""
    if _has_valid_marker(flagged_text) and (concept is None or is_canonical):
        if concept is None:
            reason = "this name has no matching entry in docs/reuse-register.yml, so a marker is not honoured on any of its definitions"
        else:
            reason = "it is the canonical definition, which always counts"
        dead_marker_note = "\n  This line carries a reuse-ok marker, but it has no effect here: %s." % reason

    if concept is None:
        base = _unregistered_finding(
            "RG-1b", "macro '%s'" % name, flagged, flagged_line, files,
            {f: loc[0] for f, loc in locations.items()},
        )
        return base + dead_marker_note

    concept_id = concept["id"]
    canonical_use = (concept.get("canonical") or {}).get("use", canonical_path)

    if is_canonical:
        body = (
            "%s:%d [reuse:%s/%s] is the canonical definition of macro '%s', which has %d "
            "definitions; also at %s.\n"
            "  It counts because the other copies still exist. Lower this number by removing "
            "a copy or getting one accepted under allowed_specialisations for '%s' in "
            "docs/reuse-register.yml -- marking this, the canonical, line has no effect."
            % (flagged, flagged_line, RULE_NAMES["RG-1b"], concept_id, name, len(files),
               also_at, concept_id)
        )
        return body + dead_marker_note

    what = "one of %d definitions of macro '%s'; also at %s" % (len(files), name, also_at)
    return _format_finding("RG-1b", flagged, flagged_line, concept_id, what,
                            canonical_path, canonical_use) + dead_marker_note


# ---------------------------------------------------------------- RG-2 scan


def _rg2_candidates(root: str) -> list[tuple[str, str]]:
    candidates = []
    for path, rel in _walk_files(root, "app", (".html", ".j2", ".jinja")):
        if rel.startswith("app/static/"):
            continue
        if _excluded(rel, DIAGRAM_EXCLUDED):
            continue
        candidates.append((path, rel))
    for path, rel in _walk_files(root, "app/static/js", (".js",)):
        if _excluded(rel, DIAGRAM_EXCLUDED):
            continue
        candidates.append((path, rel))
    for path, rel in _walk_files(root, "app/modules", (".js",)):
        if "/static/" not in rel:
            continue
        if _excluded(rel, DIAGRAM_EXCLUDED):
            continue
        candidates.append((path, rel))
    return candidates


def scan_rg2(root: str) -> list[str]:
    register = _load_register(root)
    concept = _concept_for_diagram(register)

    seen: set[tuple[str, str]] = set()
    findings = []
    for path, rel in _rg2_candidates(root):
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        # Comments are blanked for the allowance test ONLY; the library
        # match just below always reads `text` unmodified.
        composer_page = _grants_composer_allowance(text)
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = DIAGRAM_LIB_PATTERN.search(line)
            if not match:
                continue
            if _has_valid_marker(line):
                continue
            lib = match.group(1)
            if lib in COMPOSER_ALLOWED_LIBS and composer_page:
                continue
            key = (rel, lib)
            if key in seen:
                continue
            seen.add(key)
            findings.append(_rg2_finding(rel, lineno, lib, concept))
    findings.sort()
    return findings


def _rg2_finding(rel: str, lineno: int, lib: str, concept: dict | None) -> str:
    if concept is None:
        return _unregistered_finding("RG-2", "a %s library reference" % lib, rel, lineno, [rel], {rel: lineno})

    concept_id = concept["id"]
    canonical = concept.get("canonical") or {}
    canonical_path = (canonical.get("paths") or [None])[0] or \
        "app/static/js/archimate/composer_renderer.js"
    canonical_use = canonical.get("use", "ComposerRenderer.create(...)")

    what = "references the %s library; model diagrams are drawn by the ArchiMate renderer" % lib
    return _format_finding("RG-2", rel, lineno, concept_id, what, canonical_path, canonical_use)


SCANNERS = {"RG-1": scan_rg1, "RG-1b": scan_rg1b, "RG-2": scan_rg2}


def _count_valid_markers(root: str) -> int:
    """Every line, across the trees RG-1/RG-1b/RG-2 scan, carrying a valid
    marker -- the design's named mitigation against silent marker overuse:
    the number is printed, not just left to be found."""
    files: set[str] = set()
    for path, rel in _walk_files(root, "app", MACRO_SCOPE_EXTS):
        if not _excluded(rel, MACRO_EXCLUDED):
            files.add(path)
    for path, _rel_path in _rg2_candidates(root):
        files.add(path)

    count = 0
    for path in sorted(files):
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if _has_valid_marker(line):
                        count += 1
        except OSError:
            continue
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rule", choices=sorted(SCANNERS), help="which rule to run (RG-1, RG-1b or RG-2)")
    parser.add_argument("--count", action="store_true", help="print only the finding count")
    parser.add_argument("--root", default=ROOT, help="scan this tree instead of the real repository")
    parser.add_argument("--summary", action="store_true",
                         help="print every rule's count against its baseline")
    args = parser.parse_args(argv)

    root = os.path.abspath(args.root)

    if args.summary:
        baseline = _load_baseline(root)
        for rule in sorted(SCANNERS):
            count = len(SCANNERS[rule](root))
            base = baseline.get(BASELINE_KEYS[rule])
            state = "no baseline recorded" if base is None else ("ok" if count <= base else "OVER baseline")
            print("%s (%s): %d finding(s), baseline %s -- %s"
                  % (rule, RULE_NAMES[rule], count, base if base is not None else "?", state))
        print("valid reuse-ok markers found: %d" % _count_valid_markers(root))
        return 0

    if not args.rule:
        parser.error("--rule RG-1|RG-1b|RG-2 is required unless --summary is given")

    findings = SCANNERS[args.rule](root)

    if not args.count:
        for finding in findings:
            print(finding)
            print()
        baseline = _load_baseline(root).get(BASELINE_KEYS[args.rule])
        baseline_text = str(baseline) if baseline is not None else "not set"
        print("%d finding(s); baseline %s. The count may fall, not rise."
              % (len(findings), baseline_text))
    print(len(findings))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
