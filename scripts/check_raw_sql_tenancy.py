#!/usr/bin/env python
"""Find raw SQL that reads a tenant-scoped table without an organization predicate,
and raw SQL that writes organization_id outside the one command allowed to.

    python scripts/check_raw_sql_tenancy.py                       # both rules, report
    python scripts/check_raw_sql_tenancy.py --count               # reads only, count
    python scripts/check_raw_sql_tenancy.py --count --rule writes # writes only, count
    python scripts/check_raw_sql_tenancy.py --json

Two rules live here because they are the same shape of problem -- a raw SQL
string naming a tenant table, found by walking string literals rather than
trusting a comment -- and one cached tenant-table list serves both.

RULE 1 (reads, the original rule; still the default and the only rule
``--count``/``--json`` run when ``--rule`` is not given, so the existing
zero-tolerance gate this file has always driven is untouched). See below.

RULE 2 (writes). A backfill that assigns organization_id to a pre-existing
row is a one-time repair, and several of them accumulated by separate
decision before the policy in app/commands/backfill_layer_tenancy.py
existed: the next one would be another. This rule fails on any
``UPDATE <tenant table> ... SET organization_id`` string found outside that
one file, in app/ or scripts/. The dedicated commands that predate the
policy are the counted, falling ratchet baseline, not an exemption -- they
are not marked ``tenancy-ok`` for existing here; only a marker whose reason
names a concrete retirement date or the command's own deletion ticket
silences a finding, because a marker that says nothing checkable would let
any future command opt out the same way.

Why
---
Multi-tenancy is enforced by a `do_orm_execute` listener that rewrites ORM
statements. Raw `db.session.execute(text(...))` is not an ORM statement, so it
goes to the database exactly as written. Nothing in the codebase noticed the
difference, and the convention that grew up instead was a comment:

    _org_filter3 = ""
    _org_params3 = {}
    capabilities = db.session.execute(  # tenant-filtered
        text(f"... FROM business_capability {_org_filter3} ORDER BY name"),

That comment has now been wrong twice. Ten queries in
business_capability_management_routes.py returned every organisation's rows, and
the dashboard's capability-coverage metric counted every organisation's mappings
and then divided by one organisation's application total, producing a percentage
that could exceed 100%.

A comment is not a control. This is.

What it does and does not catch
-------------------------------
It reports a raw SQL string that names a table backed by a TenantMixin model and
contains no `organization_id` anywhere in the statement. That is the exact shape
of both real findings.

It deliberately does NOT try to judge the far more common "scoped via parent FK"
case, where a query filters on a parent that was itself tenant-scoped. Deciding
whether the parent really was scoped needs to follow the value backwards through
the caller, which a regex cannot do — an earlier attempt to flag those produced
334 hits, essentially all false positives, and a check nobody believes is a check
nobody runs.

So a clean result here does not mean tenancy is proven. It means the one
mechanically detectable failure — no predicate at all — is absent.

Exemptions
----------
Append `tenancy-ok: <reason>` on the line to record a deliberate exception, the
same convention `design-tokens-ok` uses. Aggregates that are genuinely global,
and CLI paths with no request context, are the expected users of it. Rule 2
honours the same marker only when the reason also names a retirement date or
a deletion ticket, in plain words.

Proven-against: a synthetic `UPDATE business_capability SET organization_id = 1`
string added to a file under app/, measured the same way the gate measures it
(`--count --rule writes`, as a subprocess) -- the count rose by one on the
spot and returned to baseline once the file was removed; automated as
tests/test_check_raw_sql_tenancy_writes.py.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Populated from the ORM at run time; falls back to a cached list so the check
# still works without a database.
CACHE = os.path.join(REPO_ROOT, "scripts", "tenant_tables.txt")

SQL_VERB = re.compile(r"\b(SELECT|UPDATE|DELETE\s+FROM|INSERT\s+INTO)\b", re.I)
FROM_TABLE = re.compile(r"\b(?:FROM|JOIN|UPDATE|INTO)\s+([a-z_][a-z0-9_]*)", re.I)


# `organization_id` as a WHOLE identifier. Without the leading boundary,
# `vendor_organization_id` matched and was accepted as tenant scoping,
# which silently passed two live cross-tenant leaks.
_ORG_COL = re.compile(r"(?<![A-Za-z0-9_])organization_id(?![A-Za-z0-9_])")

# Rule 2: a write. The table name is captured generically, the same way
# FROM_TABLE is, and checked against tenant_tables() afterwards rather than
# being baked into the pattern -- one regex for every table, not one per
# table. A table name given as an interpolated variable (`UPDATE "{table}"`)
# does not match this pattern at all: _string_parts turns that into the
# literal text `{table}`, which is not `[A-Za-z_][A-Za-z0-9_]*`. That is the
# same value-flow limit this file's own docstring already admits for the
# parent-FK case on the read side -- a regex cannot chase a variable back to
# its assignment either.
_WRITE_ORG = re.compile(
    r'\bUPDATE\s+"?([A-Za-z_][A-Za-z0-9_]*)"?\s+(?:AS\s+\w+\s+)?(?:\w+\s+)?SET\s+organization_id\b',
    re.I,
)

# A marker on a rule-2 finding is honoured only when it names something
# checkable -- a retirement date or the command's own deletion ticket -- not
# a bare "tenancy-ok: legacy" that would let any command opt out the same way
# the dedicated commands are counted, not excused, for.
_WRITE_MARKER_OK = re.compile(
    r"tenancy-ok:[^\n]*\b(?:\d{4}-\d{2}-\d{2}|deletion ticket)\b", re.I
)

# The one file rule 2 does not report on: it is the policy's home, not a
# violation of it.
_CANONICAL_BACKFILL = "app/commands/backfill_layer_tenancy.py"


def tenant_tables() -> set[str]:
    try:
        from app import create_app, db  # noqa: F401
        from app.models.mixins import TenantMixin

        create_app("testing")
        found = {
            m.class_.__tablename__
            for m in db.Model.registry.mappers
            if issubclass(m.class_, TenantMixin) and hasattr(m.class_, "__tablename__")
        }
        if found:
            with io.open(CACHE, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(sorted(found)) + "\n")
            return found
    except Exception:  # noqa: BLE001 — fall back rather than fail the gate
        pass
    if os.path.exists(CACHE):
        return {ln.strip() for ln in io.open(CACHE, encoding="utf-8") if ln.strip()}
    return set()


def _string_parts(node: ast.AST) -> str:
    """Flatten a literal / f-string / concatenation into searchable text."""
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append(sub.value)
        elif isinstance(sub, ast.Name):
            out.append(f"{{{sub.id}}}")
        elif isinstance(sub, ast.Attribute):
            out.append(f"{{{sub.attr}}}")
    return " ".join(out)


def scan_file(path: str, tables: set[str]) -> list[tuple[int, str, str]]:
    try:
        source = io.open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines()
    findings = []
    for node in ast.walk(tree):
        # Only text(...) / db.text(...) calls — that is what bypasses the listener.
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = getattr(fn, "attr", None) or getattr(fn, "id", None)
        if name != "text" or not node.args:
            continue
        sql = _string_parts(node.args[0])
        if not SQL_VERB.search(sql):
            continue
        hit = {t for t in FROM_TABLE.findall(sql) if t.lower() in tables}
        if not hit:
            continue
        # The whole statement, not just the string: the predicate is often built
        # into a variable interpolated in, or passed as a bound parameter.
        # Strip Python comments before looking for the predicate. The window is
        # searched because the clause is often built into a variable a few lines
        # up - but a COMMENT mentioning organization_id is not scoping, and this
        # gate accepted one. Proved by mutation: deleting a real org_scope() call
        # left the count at zero, because the comment above it explaining the fix
        # contained the word. This file's own docstring says 'a comment is not a
        # control'. It was one here.
        _window = lines[max(0, node.lineno - 10): node.lineno + 12]
        # Two different questions, two different windows.
        #   seg_raw  - does a deliberate `tenancy-ok:` marker exist? Markers
        #              live in comments by design, like the repo's other
        #              per-line hatches, so this must keep them.
        #   seg      - is there an actual organization_id predicate? A
        #              comment merely MENTIONING the column is not one, and
        #              accepting it made this gate satisfiable by prose -
        #              the precise failure its own docstring names.
        seg_raw = "".join(_window)
        seg = "\n".join(re.sub(r"#.*$", "", ln) for ln in _window)
        # Scoping evidence, in order of directness:
        #   - the predicate is written into the statement, or
        #   - a clause built by a helper is interpolated into it.
        # The first version looked only for a literal "organization_id" and so
        # reported the queries fixed by _org_scope() as unscoped — it scored
        # identically before and after that fix, which is the definition of a
        # check that measures nothing. Caught by reverting a known fix and
        # seeing the number not move.
        # A WORD-boundary match, not a substring. `vendor_organization_id` is a
        # different column on a different table, and the substring test accepted
        # it as proof of tenant scoping: it silently passed two live leaks in
        # routes_vendor_analysis.py and routes_hybrid_mapping.py, each of which
        # listed every organisation's elements by name. A false negative in a
        # gate is worse than a false positive, because nobody goes looking.
        if _ORG_COL.search(seg) or _ORG_COL.search(sql):
            continue
        # A clause built by the canonical helper and interpolated INTO this
        # statement. Both halves are required. An earlier version accepted any
        # `_org_*` name appearing nearby, which meant renaming the interpolated
        # variable while leaving an unused `_org_params` in scope still read as
        # scoped — the mutation test moved the count by zero, so the gate could
        # not have caught the regression it exists for.
        # The statement must interpolate a variable whose NAME says it carries the
        # org predicate. Two weaker rules were tried and both failed their own
        # mutation test: "any _org_* name nearby" and "the helper is nearby AND
        # something is interpolated". Neither could tell which variable actually
        # went into the SQL, so renaming the interpolated one while leaving an
        # unused _org_params in scope still read as scoped and the count moved by
        # zero. Requiring "org" in the interpolated name is checkable with a
        # regex and makes the convention explicit.
        if re.search(r"\{[A-Za-z0-9_]*org[A-Za-z0-9_]*\}", sql, re.I):
            continue
        if "tenancy-ok" in seg_raw:
            continue
        findings.append((node.lineno, sorted(hit)[0], sql.strip()[:90]))
    return findings


class _StringLiteralVisitor(ast.NodeVisitor):
    """Every top-level string literal in a module, one flattened text each --
    except a module/function/class's own docstring, which documents code, and
    is not SQL a request will ever send.

    Without that exclusion this file failed on itself: its own docstring, in
    prose, names the exact synthetic string its Proven-against line records
    watching the gate fail on.

    `ast.walk` alone would also visit a multi-line f-string's own
    Constant/Name parts a second time, as if they were separate statements --
    the same text searched twice under two different line numbers.
    Overriding `visit_JoinedStr` to fold an f-string via `_string_parts` and
    not descending further stops that double count.
    """

    def __init__(self) -> None:
        self.found: list[tuple[int, str]] = []

    def _visit_body_skipping_docstring(self, node: ast.AST) -> None:
        body = list(node.body)  # type: ignore[attr-defined]
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]
        for child in body:
            self.visit(child)

    def visit_Module(self, node: ast.Module) -> None:  # noqa: N802
        self._visit_body_skipping_docstring(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_body_skipping_docstring(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_body_skipping_docstring(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._visit_body_skipping_docstring(node)

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        if isinstance(node.value, str):
            self.found.append((node.lineno, node.value))

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:  # noqa: N802
        self.found.append((node.lineno, _string_parts(node)))


def scan_file_writes(path: str, tables: set[str]) -> list[tuple[int, str, str]]:
    """Rule 2: `UPDATE <tenant table> ... SET organization_id` outside the
    one command that owns that write."""
    try:
        source = io.open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines()
    visitor = _StringLiteralVisitor()
    visitor.visit(tree)
    findings = []
    for lineno, blob in visitor.found:
        for m in _WRITE_ORG.finditer(blob):
            table = m.group(1).lower()
            if table not in tables:
                continue
            _window = lines[max(0, lineno - 10): lineno + 12]
            seg_raw = "".join(_window)
            if "tenancy-ok" in seg_raw and _WRITE_MARKER_OK.search(seg_raw):
                continue
            findings.append((lineno, table, blob.strip()[:90]))
    return findings


def _iter_py_files(*dirnames: str):
    for dirname in dirnames:
        for root, dirs, files in os.walk(os.path.join(REPO_ROOT, dirname)):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fn in files:
                if fn.endswith(".py"):
                    yield os.path.join(root, fn)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--count", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--rule",
        choices=["reads", "writes"],
        action="append",
        help="reads: no organization_id predicate at all (rule 1). writes: "
             "UPDATE ... SET organization_id outside the canonical backfill "
             "(rule 2). Repeatable. Omitted together with --count or --json "
             "keeps the original reads-only behaviour the zero-tolerance "
             "raw-sql-tenancy gate has always measured; omitted with neither "
             "runs both, for a human reading the full report.",
    )
    args = parser.parse_args(argv)

    tables = tenant_tables()
    if not tables:
        print("FAIL: could not determine the tenant-scoped tables", file=sys.stderr)
        return 1

    if args.rule:
        rules = list(dict.fromkeys(args.rule))
    elif args.count or args.json:
        rules = ["reads"]
    else:
        rules = ["reads", "writes"]

    read_results = []
    if "reads" in rules:
        for path in _iter_py_files("app"):
            for lineno, table, sql in scan_file(path, tables):
                read_results.append({
                    "file": os.path.relpath(path, REPO_ROOT).replace("\\", "/"),
                    "line": lineno, "table": table, "sql": sql,
                })

    write_results = []
    if "writes" in rules:
        for path in _iter_py_files("app", "scripts"):
            rel = os.path.relpath(path, REPO_ROOT).replace("\\", "/")
            if rel == _CANONICAL_BACKFILL:
                continue
            for lineno, table, blob in scan_file_writes(path, tables):
                write_results.append({"file": rel, "line": lineno, "table": table, "sql": blob})

    if args.count:
        print(len(read_results) + len(write_results))
        return 0
    if args.json:
        payload = {"tenant_tables": len(tables)}
        if "reads" in rules:
            payload["findings"] = read_results
        if "writes" in rules:
            payload["write_findings"] = write_results
        print(json.dumps(payload, indent=2))
        return 1 if (read_results or write_results) else 0

    if "reads" in rules:
        for r in read_results:
            print(f"  {r['file']}:{r['line']}  [{r['table']}]  {r['sql']}")
        if read_results:
            print(f"\n{len(read_results)} raw SQL statement(s) read a tenant-scoped table with no "
                  f"organization_id predicate, across {len(tables)} tenant tables.")
            print("Scope the query, or append 'tenancy-ok: <reason>' if it is deliberately global.")
        else:
            print(f"No unscoped raw SQL found against {len(tables)} tenant-scoped tables.")

    if "writes" in rules:
        for r in write_results:
            print(f"  {r['file']}:{r['line']}: writes organization_id on tenant table "
                  f"{r['table']} outside the canonical backfill")
        if write_results:
            print(f"\n{len(write_results)} statement(s) write organization_id on a tenant "
                  f"table outside {_CANONICAL_BACKFILL}.")
            print("Move the write into that command, or append 'tenancy-ok: <reason>' naming "
                  "a retirement date or a deletion ticket.")
        else:
            print(f"No organization_id write found outside {_CANONICAL_BACKFILL}.")

    return 1 if (read_results or write_results) else 0


if __name__ == "__main__":
    sys.exit(main())
