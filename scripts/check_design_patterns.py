#!/usr/bin/env python
"""Design-pattern conformance checker (report-only on the first run).

Reads ``design/patterns.json`` (the rulebook, ``conformance`` section) and
``design/tier1_routes.txt`` (the templates whose errors block CI), walks every
``.html`` file under ``app/templates`` and the modules' ``templates`` folders,
and reports markup that departs from the canonical components. Static only:
no Flask, no Jinja, no template engine, no execution. Standard library only.

    python scripts/check_design_patterns.py                 # every template, console table
    python scripts/check_design_patterns.py --tier 1        # only the tier-1 templates
    python scripts/check_design_patterns.py --format json   # console as JSON
    python scripts/check_design_patterns.py --report design-audit/conformance-report.json

Exit codes: 0 no tier-1 errors; 1 one or more tier-1 errors; 2 the script
could not run (rulebook, tier list or template folder missing or unreadable).

Every rule is one function ``rule_<name>(src, ctx) -> list[Violation]`` and is
tested on its own in ``tests/test_check_design_patterns.py``. Rules read the
template source as text; where a construct cannot be recognised reliably from
text (nested HTML, markup built in a Jinja variable), the rule says so in its
docstring and the first-run report lists it under "not checked".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS_PATH = ROOT / "design" / "patterns.json"
TIER1_PATH = ROOT / "design" / "tier1_routes.txt"
TEMPLATE_ROOTS = [ROOT / "app" / "templates"] + sorted((ROOT / "app" / "modules").glob("*/templates"))
DEFAULT_REPORT = ROOT / "design-audit" / "conformance-report.json"

SEVERITY = {
    "SHADOW": "error", "ROUNDED_XL_CARD": "error", "RAW_BUTTON": "error", "RAW_TABLE": "error",
    "RAW_MODAL": "error", "BANNED_PATTERN": "error", "HARDCODED_HEX": "warn", "INPUT_HEIGHT": "error",
    "MULTI_COLUMN_FORM": "error", "NAMED_BUTTON": "error", "NAMED_INPUT": "warn", "MIXED_CURRENCY": "warn",
    "MATRIX_STICKY": "error", "MATRIX_LEGEND": "error",
}
FIX = {
    "SHADOW": "Remove the shadow. Depth comes from hairline borders, not shadows.",
    "ROUNDED_XL_CARD": "Use rounded-lg (8px).",
    "RAW_BUTTON": "Use the button macro or add a canonical variant class.",
    "RAW_TABLE": "Use the dense_table macro or add the dense-table class.",
    "RAW_MODAL": "Use the dialog macro or add aria-modal=\"true\" and the canonical dialog class.",
    "BANNED_PATTERN": "Remove the banned pattern. See patterns.json for the approved alternatives.",
    "HARDCODED_HEX": "Replace with a token from register.json.",
    "INPUT_HEIGHT": "Use 36px height (h-9).",
    "MULTI_COLUMN_FORM": "Forms are single-column. See patterns.json pilot_patterns.form.",
    "NAMED_BUTTON": "Add aria-label describing the action.",
    "NAMED_INPUT": "Add a visible label or aria-label.",
    "MIXED_CURRENCY": "One currency per template. Use the org currency token.",
    "MATRIX_STICKY": "See patterns.json matrix spec.",
    "MATRIX_LEGEND": "Add the legend. See patterns.json matrix spec.",
}


@dataclass
class Violation:
    file: str
    line: int
    rule: str
    matched_text: str
    suggested_fix: str
    tier: int = 2
    severity: str = "error"


class Ctx:
    """What a rule may need beyond the source: the rulebook and the file's path."""

    def __init__(self, conformance: dict, rel: str):
        self.conformance = conformance
        self.rel = rel
        canon = conformance.get("canonical", {})
        self.button_tokens = set(canon.get("button", {}).get("accepted_tokens", []))
        self.button_file = canon.get("button", {}).get("file")
        self.table_class = canon.get("table", {}).get("required_class", "dense-table")
        self.table_files = set(canon.get("table", {}).get("files", []))
        self.dialog_files = set(canon.get("dialog", {}).get("files", []))
        self.dialog_required = canon.get("dialog", {}).get("required_tokens", ['role="dialog"', 'aria-modal="true"'])
        self.input_height = canon.get("input", {}).get("height_class", "h-9")
        self.banned = list(conformance.get("banned_patterns", []))


# --------------------------------------------------------------------------- helpers
COMMENT_RE = re.compile(r"\{#.*?#\}", re.S)
CLASS_ATTR_RE = re.compile(r'\bclass\s*=\s*"([^"]*)"', re.S)
STYLE_ATTR_RE = re.compile(r'\bstyle\s*=\s*"([^"]*)"', re.S)
DATA_ATTR_RE = re.compile(r'\bdata-[\w-]+\s*=\s*"([^"]*)"', re.S)
TAG_RE = re.compile(r"<(\w+)\b[^>]*>", re.S)


def blank_comments(src: str) -> str:
    """Jinja comments are blanked, line breaks kept, so positions and line numbers stay valid."""
    return COMMENT_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), src)


def line_of(src: str, pos: int) -> int:
    return src.count("\n", 0, pos) + 1


def class_tokens(value: str) -> list[str]:
    """Class tokens with Jinja expressions removed, so '{{ x }} rounded-lg' yields ['rounded-lg']."""
    cleaned = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " ", value, flags=re.S)
    return cleaned.split()


def opening_tags(src: str, name: str):
    """Yield (match) for each opening tag of the given element name."""
    return re.finditer(r"<%s\b[^>]*>" % name, src, re.S)


def attr(tag: str, name: str) -> str | None:
    m = re.search(r'\b%s\s*=\s*"([^"]*)"' % re.escape(name), tag, re.S)
    return m.group(1) if m else None


def shorten(text: str, n: int = 100) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _v(ctx: Ctx, src: str, pos: int, rule: str, matched: str) -> Violation:
    return Violation(ctx.rel, line_of(src, pos), rule, shorten(matched), FIX[rule], severity=SEVERITY[rule])


# --------------------------------------------------------------------------- rules
SHADOW_TOKEN = re.compile(r"^(?:[\w-]+:)*(?:drop-)?shadow(?:-[a-z0-9]+)?$")


def rule_shadow(src: str, ctx: Ctx) -> list[Violation]:
    """A shadow utility in a class attribute (shadow, shadow-md, drop-shadow-lg, hover:shadow)."""
    out = []
    for m in CLASS_ATTR_RE.finditer(src):
        hits = [t for t in class_tokens(m.group(1)) if SHADOW_TOKEN.match(t) and not t.endswith("shadow-none")]
        if hits:
            out.append(_v(ctx, src, m.start(), "SHADOW", 'class="%s"' % m.group(1)))
    return out


def rule_rounded_xl_card(src: str, ctx: Ctx) -> list[Violation]:
    """rounded-xl or rounded-2xl on a card-like container (class names a card, or data-slot="card")."""
    out = []
    for m in TAG_RE.finditer(src):
        tag = m.group(0)
        cls = attr(tag, "class")
        if not cls:
            continue
        toks = class_tokens(cls)
        if not any(t in ("rounded-xl", "rounded-2xl") for t in toks):
            continue
        cardlike = any("card" in t for t in toks) or attr(tag, "data-slot") == "card"
        if cardlike:
            out.append(_v(ctx, src, m.start(), "ROUNDED_XL_CARD", 'class="%s"' % cls))
    return out


def rule_raw_button(src: str, ctx: Ctx) -> list[Violation]:
    """A <button> whose class carries none of the canonical variant tokens. The button macro's own
    element (the file named in patterns.json) is the canonical and is skipped."""
    if ctx.rel == ctx.button_file:
        return []
    out = []
    for m in opening_tags(src, "button"):
        cls = attr(m.group(0), "class") or ""
        if "{{" in cls and ("classes" in cls or "variant" in cls):
            continue  # class string built by the macro's own variables
        toks = set(class_tokens(cls))
        if not (toks & ctx.button_tokens):
            out.append(_v(ctx, src, m.start(), "RAW_BUTTON", m.group(0)))
    return out


def rule_raw_table(src: str, ctx: Ctx) -> list[Violation]:
    """A <table> whose class lacks the required table class. The table macros' own elements are
    reported too: on a branch where the macro does not yet emit the class, that is the finding."""
    out = []
    for m in opening_tags(src, "table"):
        toks = class_tokens(attr(m.group(0), "class") or "")
        if ctx.table_class not in toks:
            out.append(_v(ctx, src, m.start(), "RAW_TABLE", m.group(0)))
    return out


X_SHOW_DIALOG = re.compile(r'\bx-show\s*=\s*"[^"]*(?:[Oo]pen|[Mm]odal)[^"]*"')
ROLE_DIALOG = re.compile(r'\brole\s*=\s*"(?:alert)?dialog"')
OVERLAY_CLASS = re.compile(
    r'\bclass\s*=\s*"[^"]*\bfixed\b[^"]*\binset-0\b|\bclass\s*=\s*"[^"]*\binset-0\b[^"]*\bfixed\b')


def _covers_the_screen(tag: str) -> bool:
    """An element shown on an open flag is a dialog only when it takes over the screen (fixed and
    full-inset) or says it is modal. A filter popover or a row-actions menu opens on the same kind
    of flag but sits beside its trigger, and a backdrop hidden from assistive technology is
    decoration; giving either dialog semantics would be wrong."""
    if 'aria-hidden="true"' in tag:
        return False
    return 'aria-modal="true"' in tag or OVERLAY_CLASS.search(tag) is not None


def rule_raw_modal(src: str, ctx: Ctx) -> list[Violation]:
    """A <div> that is a dialog (role="dialog", or x-show on an open/modal flag) without the
    required dialog tokens on its own opening tag. The dialog and modal macro files are the
    canonical and are skipped."""
    if ctx.rel in ctx.dialog_files:
        return []
    out = []
    for m in opening_tags(src, "div"):
        tag = m.group(0)
        is_dialog = bool(ROLE_DIALOG.search(tag)) or (
            X_SHOW_DIALOG.search(tag) is not None and _covers_the_screen(tag))
        if not is_dialog:
            continue
        if all(tok in tag for tok in ctx.dialog_required):
            continue
        out.append(_v(ctx, src, m.start(), "RAW_MODAL", tag))
    return out


def rule_banned_pattern(src: str, ctx: Ctx) -> list[Violation]:
    """A banned pattern name inside a class or data- attribute, as a whole word."""
    out = []
    if not ctx.banned:
        return out
    word = re.compile(r"(?<![\w-])(%s)(?![\w-])" % "|".join(re.escape(b) for b in ctx.banned))
    for rx in (CLASS_ATTR_RE, DATA_ATTR_RE):
        for m in rx.finditer(src):
            hit = word.search(m.group(1))
            if hit:
                out.append(_v(ctx, src, m.start(), "BANNED_PATTERN", "%s in %s" % (hit.group(1), m.group(0))))
    return out


HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
VAR_DEF_RE = re.compile(r"--[\w-]+\s*:\s*#[0-9a-fA-F]{3,8}")


def rule_hardcoded_hex(src: str, ctx: Ctx) -> list[Violation]:
    """A hex colour inside a class or style attribute, excluding custom-property definitions
    (--color-x: #...). Hex inside a Jinja variable assignment or a <style> block is not checked."""
    out = []
    for rx in (CLASS_ATTR_RE, STYLE_ATTR_RE):
        for m in rx.finditer(src):
            body = VAR_DEF_RE.sub("", m.group(1))
            if HEX_RE.search(body):
                out.append(_v(ctx, src, m.start(), "HARDCODED_HEX", m.group(0)))
    return out


HEIGHT_TOKEN = re.compile(r"^h-(?:\d+(?:\.\d+)?|\[[^\]]+\]|px|full|screen|auto|fit|min|max)$")
TEXTLIKE_TYPES_EXCLUDED = {"checkbox", "radio", "hidden", "file", "range", "color", "submit", "button", "image", "reset"}


def rule_input_height(src: str, ctx: Ctx) -> list[Violation]:
    """An <input> or <select> whose class sets a height other than the canonical one. Inputs that
    are not text controls (checkbox, radio, hidden, file, range, colour, buttons) are skipped."""
    out = []
    for name in ("input", "select"):
        for m in opening_tags(src, name):
            tag = m.group(0)
            typ = (attr(tag, "type") or "text").lower()
            if name == "input" and typ in TEXTLIKE_TYPES_EXCLUDED:
                continue
            toks = class_tokens(attr(tag, "class") or "")
            heights = [t for t in toks if HEIGHT_TOKEN.match(t)]
            if heights and any(h != ctx.input_height and h not in ("h-full", "h-auto") for h in heights):
                out.append(_v(ctx, src, m.start(), "INPUT_HEIGHT", tag))
    return out


FORM_BLOCK_RE = re.compile(r"<form\b.*?</form>", re.S)
MULTI_COL_RE = re.compile(r"(?<![\w-])(?:[\w-]+:)?grid-cols-[234](?![\w-])")


def rule_multi_column_form(src: str, ctx: Ctx) -> list[Violation]:
    """grid-cols-2/3/4 inside a <form> ... </form> block. A form split across includes or built
    inside a macro call block is not seen; the first-run report says so."""
    out = []
    for f in FORM_BLOCK_RE.finditer(src):
        for m in MULTI_COL_RE.finditer(f.group(0)):
            out.append(_v(ctx, src, f.start() + m.start(), "MULTI_COLUMN_FORM", m.group(0)))
    return out


BUTTON_BLOCK_RE = re.compile(r"<button\b[^>]*>(.*?)</button>", re.S)


def rule_named_button(src: str, ctx: Ctx) -> list[Violation]:
    """A <button> with no text content and no aria-label, aria-labelledby or title. Text that is a
    Jinja expression counts as text (it is filled at render time)."""
    out = []
    for m in BUTTON_BLOCK_RE.finditer(src):
        tag = re.match(r"<button\b[^>]*>", m.group(0), re.S).group(0)
        if any(attr(tag, a) is not None for a in ("aria-label", "aria-labelledby", "title")) or ":aria-label" in tag:
            continue
        inner = m.group(1)
        if "{{" in inner or "{%" in inner:
            continue
        text = re.sub(r"<[^>]+>", "", inner).strip()
        if not text:
            out.append(_v(ctx, src, m.start(), "NAMED_BUTTON", tag))
    return out


LABEL_FOR_RE = re.compile(r'<label\b[^>]*\bfor\s*=\s*"([^"]+)"', re.S)
LABEL_BLOCK_RE = re.compile(r"<label\b[^>]*>.*?</label>", re.S)


def rule_named_input(src: str, ctx: Ctx) -> list[Violation]:
    """An <input> or <select> with no label for its id, not wrapped in a <label>, and with no
    aria-label, aria-labelledby or placeholder. Hidden, submit and button inputs are skipped. A
    label that comes through a macro call (form_field(label=...)) is not seen as text, so the
    macro's own control is reported on the macro file, not on its callers."""
    out = []
    labelled_ids = set(LABEL_FOR_RE.findall(src))
    wrapped = [(m.start(), m.end()) for m in LABEL_BLOCK_RE.finditer(src)]
    for name in ("input", "select"):
        for m in opening_tags(src, name):
            tag = m.group(0)
            typ = (attr(tag, "type") or "text").lower()
            if name == "input" and typ in ("hidden", "submit", "button", "reset", "image"):
                continue
            if any(attr(tag, a) is not None for a in ("aria-label", "aria-labelledby", "placeholder")) or ":aria-label" in tag or ":placeholder" in tag:
                continue
            if any(a <= m.start() < b for a, b in wrapped):
                continue
            ident = attr(tag, "id")
            if ident and (ident in labelled_ids or "{{" in ident):
                continue
            out.append(_v(ctx, src, m.start(), "NAMED_INPUT", tag))
    return out


POUND_RE = re.compile(r"£|&pound;|&#163;")
DOLLAR_RE = re.compile(r"(?<![\w$])\$(?=\d|\{\{|&#36;)|&#36;")


def rule_mixed_currency(src: str, ctx: Ctx) -> list[Violation]:
    """Both pound and dollar signs in one template. A dollar counts only before a digit or a Jinja
    value, so JavaScript template strings and jQuery calls are not counted."""
    text = re.sub(r"<script\b.*?</script>", "", src, flags=re.S)
    p, d = POUND_RE.search(text), DOLLAR_RE.search(text)
    if p and d:
        first = min(p.start(), d.start())
        return [_v(ctx, src, first, "MIXED_CURRENCY", "£ at line %d and $ at line %d" % (line_of(src, p.start()), line_of(src, d.start())))]
    return []


MATRIX_TABLE_RE = re.compile(r'<table\b[^>]*\bclass\s*=\s*"[^"]*(?<![\w-])matrix(?![\w-])[^"]*"[^>]*>', re.S)


def rule_matrix_sticky(src: str, ctx: Ctx) -> list[Violation]:
    """A matrix table (class 'matrix') that lacks sticky-header or sticky-col."""
    out = []
    for m in MATRIX_TABLE_RE.finditer(src):
        toks = class_tokens(attr(m.group(0), "class") or "")
        if "sticky-header" not in toks or "sticky-col" not in toks:
            out.append(_v(ctx, src, m.start(), "MATRIX_STICKY", m.group(0)))
    return out


def rule_matrix_legend(src: str, ctx: Ctx) -> list[Violation]:
    """A matrix table not followed, within 50 lines of its closing tag, by a .matrix-legend block."""
    out = []
    for m in MATRIX_TABLE_RE.finditer(src):
        end = src.find("</table>", m.end())
        start = end if end != -1 else m.end()
        window = "\n".join(src[start:].split("\n")[:50])
        if "matrix-legend" not in window:
            out.append(_v(ctx, src, m.start(), "MATRIX_LEGEND", m.group(0)))
    return out


RULES = [
    rule_shadow, rule_rounded_xl_card, rule_raw_button, rule_raw_table, rule_raw_modal, rule_banned_pattern,
    rule_hardcoded_hex, rule_input_height, rule_multi_column_form, rule_named_button, rule_named_input,
    rule_mixed_currency, rule_matrix_sticky, rule_matrix_legend,
]
RULE_NAMES = [r.__name__[5:].upper() for r in RULES]


# --------------------------------------------------------------------------- driver
def check_file(path: Path, conformance: dict, rel: str) -> list[Violation]:
    src = blank_comments(path.read_text(encoding="utf-8", errors="replace"))
    ctx = Ctx(conformance, rel)
    out: list[Violation] = []
    for rule in RULES:
        out.extend(rule(src, ctx))
    return out


def rel_path(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT)).replace("\\", "/")
    except ValueError:  # a path outside the repository (a test fixture, an absolute --report)
        return str(p).replace("\\", "/")


def load_tier1(path: Path) -> tuple[set[str], list[str]]:
    """Tier-1 paths as listed (repo-relative) and the listed paths that do not exist."""
    listed, missing = set(), []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        listed.add(line.replace("\\", "/"))
        if not (ROOT / line).exists():
            missing.append(line)
    return listed, missing


def git_branch() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:  # noqa: BLE001 - any git failure means "unknown branch"
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Design-pattern conformance checker (static, report-only).")
    ap.add_argument("--tier", type=int, choices=[1], help="only check the files in design/tier1_routes.txt")
    ap.add_argument("--report", default=str(DEFAULT_REPORT), help="where to write the JSON report")
    ap.add_argument("--format", choices=["table", "json"], default="table", help="console output format")
    args = ap.parse_args(argv)

    if not PATTERNS_PATH.exists():
        print("cannot run: %s is missing" % rel_path(PATTERNS_PATH)); return 2
    try:
        patterns = json.loads(PATTERNS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("cannot run: %s could not be parsed (%s)" % (rel_path(PATTERNS_PATH), exc)); return 2
    conformance = patterns.get("conformance")
    if not isinstance(conformance, dict) or "canonical" not in conformance:
        print("cannot run: %s has no conformance section with a canonical block" % rel_path(PATTERNS_PATH)); return 2
    if not TIER1_PATH.exists():
        print("cannot run: %s is missing" % rel_path(TIER1_PATH)); return 2
    roots = [r for r in TEMPLATE_ROOTS if r.is_dir()]
    if not roots:
        print("cannot run: no template directory under app/"); return 2

    tier1, missing = load_tier1(TIER1_PATH)
    files = sorted({p for r in roots for p in r.rglob("*.html")})
    if args.tier == 1:
        files = [p for p in files if rel_path(p) in tier1]

    violations: list[Violation] = []
    for p in files:
        rel = rel_path(p)
        for v in check_file(p, conformance, rel):
            v.tier = 1 if rel in tier1 else 2
            if v.tier == 2 and v.severity == "error":
                v.severity = "warn"
            violations.append(v)
    violations.sort(key=lambda v: (v.rule, v.file, v.line))

    by_rule = {name: 0 for name in RULE_NAMES}
    by_file: dict[str, int] = {}
    by_tier = {"1": 0, "2": 0}
    for v in violations:
        by_rule[v.rule] += 1
        by_file[v.file] = by_file.get(v.file, 0) + 1
        by_tier[str(v.tier)] += 1
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "branch": git_branch(),
        "patterns_version": hashlib.sha1(PATTERNS_PATH.read_bytes()).hexdigest(),
        "template_roots": [rel_path(r) for r in roots],
        "files_scanned": len(files),
        "files_tier1": len([p for p in files if rel_path(p) in tier1]),
        "tier1_listed": len(tier1),
        "tier1_missing": missing,
        "violations": [asdict(v) for v in violations],
        "summary": {"by_rule": by_rule, "by_file": dict(sorted(by_file.items(), key=lambda kv: (-kv[1], kv[0]))), "by_tier": by_tier},
    }
    report_path = Path(args.report)
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")

    tier1_errors = [v for v in violations if v.tier == 1 and v.severity == "error"]
    if args.format == "json":
        print(json.dumps({k: report[k] for k in ("generated_at", "branch", "files_scanned", "files_tier1", "tier1_missing", "summary")}, indent=1))
    else:
        print("files scanned: %d   tier-1 files: %d   violations: %d   tier-1 errors: %d" % (len(files), report["files_tier1"], len(violations), len(tier1_errors)))
        if missing:
            print("tier-1 paths that do not exist: %s" % ", ".join(missing))
        print("%-18s %-5s %-5s %-60s %s" % ("rule", "tier", "sev", "file:line", "matched"))
        for v in violations[:30]:
            print("%-18s %-5d %-5s %-60s %s" % (v.rule, v.tier, v.severity, "%s:%d" % (v.file, v.line), v.matched_text[:70]))
        if len(violations) > 30:
            print("%d more in %s" % (len(violations) - 30, rel_path(report_path)))
    return 1 if tier1_errors else 0


if __name__ == "__main__":
    sys.exit(main())
