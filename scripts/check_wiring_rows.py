#!/usr/bin/env python
"""Find a fact-bearing model column with no row in the intelligence wiring register.

What is flagged
----------------
A `ClassDef` under `app/models/**/*.py` or `app/modules/*/models/**/*.py` (top-level, or nested inside a
function -- this tree defines several of its models that way) that either assigns `__tablename__` in its own
body or has a base class named `Model` / `db.Model`, and whose body assigns a `Column`, `db.Column` or
`mapped_column` call to a plain attribute -- `name = db.Column(...)` -- where the attribute name matches
`FAMILY_RE` (a maturity, cost, budget, tco, licence, contract, renewal, lifecycle, end-of-life, risk,
compliance, sla, owner, raci, plateau, gap, score or one of the other families named in
`docs/intelligence-wiring-register.yml`'s `pipeline_rule.statement`) is a fact-bearing column. Each family
is matched in every spelling the tree uses for it — singular, plural, past tense, agent and gerund forms,
plus `ownership`, `critical`, `compliant`, `end_of_life`, `eos`, `retired`, `licensing` — so
`end_of_life_date`, `last_assessed`, `key_risks`, `other_costs` and `assessor` are fact-bearing;
`controller`, `discount`, `scorecard` and `healthy` are not. A first
positional string argument -- `db.Column("plateau", ...)` -- is recorded as the database column name and
tested as well as the attribute name.

A fact-bearing column is covered, and not flagged, when:

  1. a `rows[]` entry in the register names the class (comma-split, case-sensitive, exact) in `model` and
     names the column in `fields` -- bare (`health_status`), as `Class.column` (`Plateau.target_date`, for a
     row whose `model` lists more than one class), or as a path expression matched on its last identifier
     (`elements[].archimate_element_id` matches `archimate_element_id`) -- or the database column name; or
  2. a `not_intelligence[]` entry's `where` text names the class or the file's basename, and later in the same
     text names the attribute or database column name; or
  3. the column's own `db.Column(...)` line, or the line directly above it (when that line is not itself
     another column's own declaration -- the same rule `check_unrendered_model_fields.py` uses), carries
     `wiring-ok: IW-nn <reason>` where `IW-nn` is a real id in the register and `<reason>` is at least two
     words. A marker whose id is not in the register does not suppress the finding -- it is reported, with a
     note that the marker has no effect, so a typo'd id cannot silently defeat the ratchet.

Scope and disclosed limits
---------------------------
Any path segment `tests` is excluded. Association tables built with `db.Table(...)` are not classes and are
out of scope — disclosed, not silent. A column named outside the families above is not seen at all; adding a
new family is a register change (`pipeline_rule.statement`), not a checker change. Coverage is by name, not by
semantics: a row naming `status` on a class covers every `status` column that class has, however different
their meaning. A column declared on a mixin (a class with no `Model` base and no `__tablename__`) is not
seen; the model that inherits it is not scanned for inherited columns. A missing or unparseable register is
a hard failure (exit 2), never a count of 0 — silence here must never look like "no debt".

Usage
-----
    python scripts/check_wiring_rows.py                         # list findings
    python scripts/check_wiring_rows.py --count                  # trailing count only
    python scripts/check_wiring_rows.py --list                    # list findings explicitly
    python scripts/check_wiring_rows.py --root <tree>              # scan a different tree (tests)
    python scripts/check_wiring_rows.py --register <path>          # read a different register (tests)
    python scripts/check_wiring_rows.py --base [ref]              # list findings added since ref (default: merge-base with origin/main)

Base diff
----------
When `--base [REF]` is given, the checker extracts the base tree's models with `git archive`, scans them
using the **branch's** register, and compares the two finding sets keyed on `(path, class, attribute)`.
Added findings are those present on the branch and absent at the base. Without `--count`, each added finding
is printed by name, then a summary line and the same remediation line; with `--count`, only the integer is
printed. Exit codes: 0 (none added), 1 (one or more added), 2 (register error), 3 (base ref unresolvable).

Proven-against: tests/test_wiring_rows_gate.py -- a positive control (an unregistered fact-bearing column is
flagged), a negative control (a register row, and separately a not_intelligence entry, suppresses it), the
escape hatch on the column's own line and any line of a multi-line declaration (and its refusal to honour a
marker whose id is not registered), the family boundary (`user_count`/`description` never flagged;
`eol_date`/`is_baseline`/`rto_hours`/`owner_id` flagged), a class nested inside a function, a database-column-
name match, a missing register exiting 2, a three-line column declaration with the marker on the closing
paren, an `AnnAssign` column shape, a mixin column not flagged, and a `--base` diff that proves a deletion-
plus-addition scenario.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# Reuse the merge-base resolution from the smoke-coverage gate.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_smoke_coverage_on_change import _base_ref  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

FAMILY_RE = re.compile(
    r"(^|_)(maturit(?:y|ies)|assess(?:ment|ments|ed|or|ors)?|health|costs?|costing|budgets?|budgeted|tco|spend(?:ing)?|"
    r"licen[cs](?:e|es|ed|ing)|renewals?|renewed|contracts?|contracted|contracting|vendors?|lifecycle|end_of_life|eol|eos|"
    r"retire(?:ment|d|s)?|decommission(?:ed|ing)?|risks?|risky|complian(?:ce|t)|controls?|slas?|availability|rto|rpo|"
    r"criticality|critical|owner(?:s|ship)?|owned|raci|usage|plateaus?|gaps?|roadmaps?|milestones?|baselines?|baselined|"
    r"drifts?|drifted|scores?|scored|scoring)(_|$)"
)

_COLUMN_CALL_NAMES = {"Column", "mapped_column"}
_ESCAPE_RE = re.compile(r"wiring-ok:\s*(IW-[0-9]{2,})(?:\s+(\S.*?))?\s*$")
_DOTTED_FIELD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)$")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class Finding:
    path: str
    lineno: int
    cls: str
    attr: str
    note: str = ""

    def render(self) -> str:
        base = f"{self.path}:{self.lineno} {self.cls}.{self.attr}"
        return f"{base} ({self.note})" if self.note else base


@dataclass
class Register:
    rows: list = field(default_factory=list)
    not_intelligence: list = field(default_factory=list)
    wiring_ids: set = field(default_factory=set)


def _load_register(path: Path) -> tuple[Register | None, str | None]:
    if yaml is None:
        return None, "PyYAML is not available; cannot read the intelligence wiring register"
    if not path.exists():
        return None, f"register file not found at {path}"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return None, f"register file at {path} did not parse: {exc}"
    if not isinstance(data, dict) or "rows" not in data or "not_intelligence" not in data:
        return None, f"register file at {path} did not parse into rows/not_intelligence"
    rows = data.get("rows") or []
    not_intelligence = data.get("not_intelligence") or []
    ids = {r["id"] for r in rows if isinstance(r, dict) and "id" in r}
    ids |= {r["id"] for r in not_intelligence if isinstance(r, dict) and "id" in r}
    return Register(rows=rows, not_intelligence=not_intelligence, wiring_ids=ids), None


def _iter_model_files(root: Path):
    seen: set[Path] = set()
    bases = [root / "app" / "models"]
    modules_dir = root / "app" / "modules"
    if modules_dir.is_dir():
        for module_dir in sorted(modules_dir.iterdir()):
            models_dir = module_dir / "models"
            if models_dir.is_dir():
                bases.append(models_dir)
    for base in bases:
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            if "tests" in p.relative_to(root).parts:
                continue
            if p not in seen:
                seen.add(p)
                yield p


def _is_column_call(node) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Name):
        return f.id in _COLUMN_CALL_NAMES
    if isinstance(f, ast.Attribute):
        return f.attr in _COLUMN_CALL_NAMES
    return False


def _first_positional_string(call: ast.Call) -> str | None:
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    return None


def _class_qualifies(cdef: ast.ClassDef) -> bool:
    for base in cdef.bases:
        name = None
        if isinstance(base, ast.Name):
            name = base.id
        elif isinstance(base, ast.Attribute):
            name = base.attr
        if name == "Model":
            return True
    for stmt in cdef.body:
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if isinstance(t, ast.Name) and t.id == "__tablename__":
                    return True
    return False


def _column_assigns(cdef: ast.ClassDef):
    """[(attr_name, db_col_name_or_None, lineno, end_lineno)] for each column declared directly in cdef's own body."""
    out = []
    for stmt in cdef.body:                              # Assign: name = db.Column(...)
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                continue
            if not _is_column_call(stmt.value):
                continue
            out.append((stmt.targets[0].id, _first_positional_string(stmt.value),
                        stmt.lineno, stmt.end_lineno or stmt.lineno))
        elif isinstance(stmt, ast.AnnAssign):            # AnnAssign: name: Mapped[T] = mapped_column(...)
            if not isinstance(stmt.target, ast.Name):
                continue
            if stmt.value is None or not _is_column_call(stmt.value):
                continue
            out.append((stmt.target.id, _first_positional_string(stmt.value),
                        stmt.lineno, stmt.end_lineno or stmt.lineno))
    return out


def _last_identifier(s: str) -> str:
    idents = _IDENT_RE.findall(s)
    return idents[-1] if idents else s


def _row_covers(row: dict, cls: str, names: set[str]) -> bool:
    model_field = row.get("model") or ""
    classes = [c.strip() for c in str(model_field).split(",") if c.strip()]
    if cls not in classes:
        return False
    for f in row.get("fields") or []:
        f = str(f)
        m = _DOTTED_FIELD_RE.match(f)
        if m:
            fcls, fcol = m.group(1), m.group(2)
            if fcls == cls and fcol in names:
                return True
            continue
        if _last_identifier(f) in names:
            return True
    return False


def _not_intelligence_covers(entry: dict, cls: str, names: set[str], basename: str) -> bool:
    where = entry.get("where") or []
    text = " ".join(str(w) for w in where)
    markers = [m for m in (cls, basename) if m and re.search(rf"\b{re.escape(m)}\b", text)]
    if not markers:
        return False
    pos = min(re.search(rf"\b{re.escape(m)}\b", text).start() for m in markers)
    rest = text[pos:]
    return any(re.search(rf"\b{re.escape(n)}\b", rest) for n in names)


def _is_covered(cls: str, attr: str, dbcol: str | None, basename: str, register: Register) -> bool:
    names = {attr}
    if dbcol:
        names.add(dbcol)
    for row in register.rows:
        if _row_covers(row, cls, names):
            return True
    for entry in register.not_intelligence:
        if _not_intelligence_covers(entry, cls, names, basename):
            return True
    return False


def _escape_marker(lines: list[str], lineno: int, end_lineno: int, column_lines: set[int]):
    candidates = []
    for ln in range(lineno, end_lineno + 1):
        if 1 <= ln <= len(lines):
            candidates.append(lines[ln - 1])
    above = lineno - 1
    if above >= 1 and above not in column_lines:
        candidates.append(lines[above - 1])
    for c in candidates:
        m = _ESCAPE_RE.search(c)
        if m:
            return m.group(1), (m.group(2) or "").strip()
    return None


def _scan_file(path: Path, root: Path, register: Register) -> list[Finding]:
    try:
        src = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    lines = src.splitlines()
    rel = str(path.relative_to(root)).replace(os.sep, "/")
    basename = path.name

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not _class_qualifies(node):
            continue
        assigns = _column_assigns(node)
        column_lines = set()
        for _a, _d, lineno, end_lineno in assigns:
            column_lines.update(range(lineno, end_lineno + 1))
        for attr, dbcol, lineno, end_lineno in assigns:
            if not FAMILY_RE.search(attr):
                continue
            if _is_covered(node.name, attr, dbcol, basename, register):
                continue
            marker = _escape_marker(lines, lineno, end_lineno, column_lines)
            if marker is not None:
                marker_id, reason = marker
                if marker_id in register.wiring_ids and len(reason.split()) >= 2:
                    continue  # suppressed
                if marker_id not in register.wiring_ids:
                    findings.append(Finding(rel, lineno, node.name, attr,
                                             note=f"wiring-ok marker {marker_id} is not in the register; has no effect"))
                    continue
            findings.append(Finding(rel, lineno, node.name, attr))
    return findings


def scan(root: Path, register: Register) -> list[Finding]:
    findings: list[Finding] = []
    for path in _iter_model_files(root):
        findings.extend(_scan_file(path, root, register))
    return findings


def _find_base(ref: str) -> str | None:
    """Resolve a base ref; return None if unresolvable."""
    proc = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                          capture_output=True, text=True, cwd=ROOT)
    return ref if proc.returncode == 0 else None


def _export_base(ref: str, dest: Path) -> Path | None:
    """Export app/models and app/modules from the base ref into dest."""
    archive_root = dest / "base"
    archive_root.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", "archive", ref, "app/models", "app/modules"],
        capture_output=True, cwd=ROOT,
    )
    if proc.returncode != 0:
        return None
    with tarfile.open(fileobj=BytesIO(proc.stdout), mode="r") as tar:
        tar.extractall(path=archive_root)
    return archive_root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", action="store_true", help="print only the trailing count")
    parser.add_argument("--list", action="store_true", help="print each finding (default when --count is absent)")
    parser.add_argument("--root", default=str(ROOT), help="scan a different tree (tests)")
    parser.add_argument("--register", default=None, help="read a different register file (tests)")
    parser.add_argument("--base", nargs="?", const="auto", default=None,
                        help="list findings added since ref (default: merge-base with origin/main)")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    register_path = Path(args.register) if args.register else root / "docs" / "intelligence-wiring-register.yml"
    register, err = _load_register(register_path)
    if register is None:
        print(err, file=sys.stderr)
        return 2

    findings = scan(root, register)

    if args.base is not None:
        # Base-diff mode.
        base_ref = _base_ref() if args.base == "auto" else args.base
        resolved = _find_base(base_ref)
        if resolved is None:
            print(f"base ref {base_ref} cannot be resolved; added-findings check not run", file=sys.stderr)
            return 3
        tmp_dir = Path(tempfile.mkdtemp(prefix="wiring-base-"))
        try:
            base_root = _export_base(resolved, tmp_dir)
            if base_root is None:
                print(f"base ref {resolved} could not be exported; added-findings check not run", file=sys.stderr)
                return 3
            base_findings = scan(base_root, register)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        base_set = {(f.path, f.cls, f.attr) for f in base_findings}
        added = [f for f in findings if (f.path, f.cls, f.attr) not in base_set]

        if args.count:
            print(len(added))
            return 1 if added else 0

        if added:
            for f in added:
                print(f.render())
            print(f"\n{len(added)} fact-bearing column(s) added since {resolved} with no row "
                  "in the intelligence wiring register.")
            print("Add a row (wired, or not_intelligence with a reason) "
                  "to docs/intelligence-wiring-register.yml, "
                  "or mark 'wiring-ok: IW-nn <reason>' on the column line.")
        else:
            print(f"No fact-bearing model columns added since {resolved} with no row "
                  "in the intelligence wiring register.")
        return 1 if added else 0

    if args.count:
        print(len(findings))
        return 0

    if findings:
        for finding in findings:
            print(finding.render())
        print(f"\n{len(findings)} fact-bearing model column(s) with no row in the intelligence wiring register.")
        print("Add a row (wired, or not_intelligence with a reason) to docs/intelligence-wiring-register.yml, "
              "or mark 'wiring-ok: IW-nn <reason>' on the column line.")
    else:
        print("No fact-bearing model columns found with no row in the intelligence wiring register.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
