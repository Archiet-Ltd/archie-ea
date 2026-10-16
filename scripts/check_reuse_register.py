#!/usr/bin/env python
"""One canonical implementation per concept, and a ratchet against a second copy.

    python scripts/check_reuse_register.py                  # check
    python scripts/check_reuse_register.py --count          # violation count only
    python scripts/check_reuse_register.py --entries        # print the register for a builder
    python scripts/check_reuse_register.py --update-baseline  # lower the baseline; never raises it

Why
---
Independent review of the last twenty rounds kept finding a second copy of a helper
that already existed: a second capability-visibility lookup, a second name normaliser,
a second owner converter, a second organisation-id reader, a second approval path. Each
was found by a reviewer, cost a fix round and a full CI run, and could have been found
by a grep. ``docs/reuse-register.yml`` is the list a builder greps first: for each
concept, the one canonical implementation and the definitions that must not appear
beside it. This script is the gate that makes the list binding.

What it checks
--------------
(a) Every ``active`` entry's canonical file exists and defines every listed symbol
    (``name`` for a module-level function or class, ``Class.method`` for a method, a
    ``{% macro name(`` for a template, a ``--name:`` custom-property declaration for
    a ``.css`` canonical, or a ``.name`` class in a selector of a ``.css`` canonical, comments excluded). A canonical that moved or was renamed fails, so the register
    cannot rot into a list of dead links.
(b) Every ``must_not_create`` regex is run over its scope (default: ``app/**/*.py``).
    A match in any file other than the canonical file or a ``known_exceptions`` path is
    a *hit*. Hits that already existed are recorded per pattern and per file in
    ``scripts/reuse_register_baseline.json``; a file may hold no more hits of a pattern than its own
    baseline, except that growth is forgiven up to the baselined hits of files that no longer exist
    (a rename). A cleanup elsewhere never makes room for a new copy.
(c) A failure prints the register entry: what to reuse, and where it lives.

Entries in status ``pending`` name a canonical implementation that is merged in another
pull request (``pending_branch``); only their patterns are enforced, and a pending
symbol that has landed is reported so the entry can be promoted to ``active``. Entries in
status ``undetermined`` record the competing implementations found (``candidates``) and
enforce nothing: the register says plainly that no one owner has been chosen.

The baseline
------------
``--update-baseline`` rewrites the baseline to the current hit counts and REFUSES to
let a pattern grow (renames excepted): lowering is routine after a cleanup, raising is done by
fixing the code, or by a ``known_exceptions`` entry (with a reason) in the register,
which shows up in review. A baseline higher than the tree is reported as slack (the
count may be lowered) but is not a failure, so a cleanup in one pull request never breaks
another.

Exemptions
----------
``reuse-ok: <reason>`` on the matching line, the line above it, or any line up to the colon that
ends a multi-line def, for a definition that only looks like a
second copy. A bare marker with no reason does not exempt.

Proven-against: a ``def normalize_name(`` added to a new file under app/ -- the gate
names the ``name-matching`` entry and fails; a canonical symbol renamed -- the gate
fails on the entry. Both are pinned in tests/test_check_reuse_register.py.

String and docstring contents are masked before matching, so advice quoted in prose is not a hit. Test
modules (a ``tests`` path segment under app/) are skipped unless a pattern's scope starts with tests.

Static: reads source text and the register only. No imports of the application, no
database.
"""

from __future__ import annotations

import argparse
import ast
import json
import io
import re
import sys
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTER = Path("docs") / "reuse-register.yml"
BASELINE = Path("scripts") / "reuse_register_baseline.json"

STATUSES = ("active", "pending", "undetermined")
SKIP_PARTS = {"__pycache__", "node_modules", ".git", "venv", ".venv"}
MARKER = re.compile(r"reuse-ok:\s*\S")
NL = chr(10)
MACRO = r"{%-?\s*macro\s+NAME\s*\("


# ---------------------------------------------------------------- register


def load_register(root: Path, register: Path | None = None):
    """(entries, problems). A missing or malformed register is a problem, never a pass."""
    path = register or (root / REGISTER)
    if not path.exists():
        return [], [f"register {path.as_posix()} does not exist"]
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return [], [f"register is not valid YAML: {exc}"]
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        return [], ["register has no entries"]
    return entries, validate(entries)


def validate(entries) -> list[str]:
    problems, seen_ids = [], set()
    for i, e in enumerate(entries):
        eid = e.get("id") if isinstance(e, dict) else None
        where = f"entry {eid or i}"
        if not isinstance(e, dict) or not eid or not e.get("concept"):
            problems.append(f"{where}: needs id and concept")
            continue
        if eid in seen_ids:
            problems.append(f"{where}: duplicate id")
        seen_ids.add(eid)
        status = e.get("status")
        if status not in STATUSES:
            problems.append(f"{where}: status must be one of {STATUSES}, got {status!r}")
            continue
        canonical = e.get("canonical") or {}
        if status == "active" and not (canonical.get("path") and canonical.get("symbols")):
            problems.append(f"{where}: an active entry needs canonical.path and canonical.symbols")
        if status == "pending" and not (canonical.get("path") and e.get("pending_branch")):
            problems.append(f"{where}: a pending entry needs canonical.path and pending_branch")
        if status == "undetermined" and (e.get("must_not_create") or canonical):
            problems.append(f"{where}: an undetermined entry names no canonical and enforces nothing")
        if status == "undetermined" and not e.get("candidates"):
            problems.append(f"{where}: an undetermined entry lists the competing candidates")
        if status != "undetermined" and not e.get("reuse"):
            problems.append(f"{where}: needs a `reuse` line telling a builder what to use")
        pids = set()
        for p in e.get("must_not_create") or []:
            pid = p.get("id") if isinstance(p, dict) else None
            if not pid or not p.get("regex"):
                problems.append(f"{where}: each must_not_create needs id and regex")
                continue
            if pid in pids:
                problems.append(f"{where}: duplicate pattern id {pid}")
            pids.add(pid)
            try:
                re.compile(p["regex"])
            except re.error as exc:
                problems.append(f"{where}/{pid}: bad regex: {exc}")
        for x in e.get("known_exceptions") or []:
            if not (isinstance(x, dict) and x.get("path") and x.get("reason")):
                problems.append(f"{where}: every known_exceptions item needs path and reason")
    return problems


# ---------------------------------------------------------------- symbols


def _py_symbols(text: str) -> set[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    out = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        out.add(f"{node.name}.{sub.name}")
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return out


def _css_symbol_defined(text: str, symbol: str) -> bool:
    """A custom property is declared (`--name:`); a class symbol (`.name`) appears in a selector, followed by a
    character that cannot continue a class name, so `.public-faq` is not found in `.public-faq-x`. `text` has
    its comments removed."""
    if symbol.startswith("."):
        return re.search(r"(?<![\w-])" + re.escape(symbol) + r"(?![\w-])", text) is not None
    return re.search(r"(?m)(^|[\s;{])" + re.escape(symbol) + r"\s*:", text) is not None


def missing_symbols(root: Path, canonical_path: str, symbols) -> tuple[bool, list[str]]:
    """(file_exists, symbols not defined there)."""
    path = root / canonical_path
    if not path.is_file():
        return False, list(symbols)
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".py":
        have = _py_symbols(text)
        return True, [s for s in symbols if s not in have]
    if path.suffix == ".css":
        uncommented = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        return True, [s for s in symbols if not _css_symbol_defined(uncommented, s)]
    return True, [s for s in symbols if not re.search(MACRO.replace("NAME", re.escape(s)), text)]


def check_canonicals(root: Path, entries) -> tuple[list[str], list[str]]:
    """(failures, notes) for rule (a)."""
    fails, notes = [], []
    for e in entries:
        status, canonical = e.get("status"), e.get("canonical") or {}
        eid = e.get("id")
        if status == "active":
            exists, missing = missing_symbols(root, canonical["path"], canonical["symbols"])
            if not exists:
                fails.append(f"[{eid}] canonical file {canonical['path']} no longer exists; "
                             f"update the register or restore the file")
            elif missing:
                fails.append(f"[{eid}] {canonical['path']} no longer defines {', '.join(missing)}; "
                             f"update the register to the new canonical")
        if status in ("active", "pending") and e.get("pending_symbols"):
            exists, missing = missing_symbols(root, canonical["path"], e["pending_symbols"])
            landed = [s for s in e["pending_symbols"] if exists and s not in missing]
            if landed:
                notes.append(f"[{eid}] pending symbol(s) {', '.join(landed)} now exist in "
                             f"{canonical['path']}: move them to canonical.symbols")
        if status == "pending":
            exists, missing = missing_symbols(root, canonical["path"], canonical.get("symbols") or [])
            if exists and not missing and canonical.get("symbols"):
                notes.append(f"[{eid}] pending canonical has landed: set status to active")
        for x in e.get("known_exceptions") or []:
            if not (root / x["path"]).exists():
                notes.append(f"[{eid}] known exception {x['path']} does not exist here")
    return fails, notes


# ---------------------------------------------------------------- hits


def _files(root: Path, scope: str, suffix: str, _cache: dict):
    key = (root, scope, suffix)
    if key not in _cache:
        base = root / scope
        _cache[key] = [] if not base.exists() else [
            p for p in sorted(base.rglob(f"*{suffix}"))
            if SKIP_PARTS.isdisjoint(p.relative_to(root).parts)
            and (scope.split("/")[0] == "tests" or "tests" not in p.relative_to(root).parts)
        ]
    return _cache[key]


def _is_exception(rel: str, entry) -> bool:
    if rel == (entry.get("canonical") or {}).get("path"):
        return True
    for x in entry.get("known_exceptions") or []:
        path = x["path"]
        if rel == path or (path.endswith("/") and rel.startswith(path)):
            return True
    return False


def mask_strings(text: str) -> str:
    """Blank the contents of string tokens (docstrings, messages) so advice quoted in prose is not a hit.

    Offsets are preserved (newlines kept), so match positions still index the original text.
    """
    starts, pos = [0], 0
    for line in text.split(NL):
        pos += len(line) + 1
        starts.append(pos)
    chars = list(text)
    fstring_middle = getattr(tokenize, "FSTRING_MIDDLE", None)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.STRING or (fstring_middle is not None and tok.type == fstring_middle):
                a = starts[tok.start[0] - 1] + tok.start[1]
                b = starts[tok.end[0] - 1] + tok.end[1]
                for i in range(a, min(b, len(chars))):
                    if chars[i] != NL:
                        chars[i] = " "
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    return "".join(chars)


def _exempt(lines: list[str], masked_lines: list[str], idx: int, col: int) -> bool:
    """`reuse-ok: <reason>` on the match, on a comment-only line directly above it, or on a later line of
    the same statement: a multi-line def or call, followed only while the bracket opened at the match is
    still open (the check_untenanted_reads convention). One marker never covers a following block."""
    if idx > 0 and lines[idx - 1].lstrip().startswith(("#", "{#")) and MARKER.search(lines[idx - 1]):
        return True
    depth = 0
    for i in range(idx, min(idx + 60, len(lines))):
        if MARKER.search(lines[i]):
            return True
        seg = masked_lines[i][col:] if i == idx else masked_lines[i]
        depth += sum(seg.count(c) for c in "([") - sum(seg.count(c) for c in ")]")
        if depth <= 0:
            break
    return False


def find_hits(root: Path, entries):
    """{(entry_id, pattern_id): {file: [line numbers]}} over the whole register."""
    listing: dict = {}  # one directory walk per scope and suffix, per run
    plan = []  # (entry, pattern, compiled, scopes, suffixes)
    for e in entries:
        if e.get("status") == "undetermined":
            continue
        for p in e.get("must_not_create") or []:
            plan.append((e, p, re.compile(p["regex"], re.MULTILINE),
                         p.get("scope") or ["app"], p.get("suffixes") or [".py"]))
    hits: dict[tuple[str, str], dict[str, list[int]]] = {(e["id"], p["id"]): {} for e, p, *_ in plan}
    wanted: dict[Path, list] = {}
    for item in plan:
        _, _, _, scopes, suffixes = item
        for scope in scopes:
            for suffix in suffixes:
                for path in _files(root, scope, suffix, listing):
                    wanted.setdefault(path, []).append(item)
    for path, items in wanted.items():
        rel = path.relative_to(root).as_posix()
        applicable = [it for it in items if not _is_exception(rel, it[0])]
        if not applicable:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = None
        masked = None
        for e, p, rx, _, _ in applicable:
            # Whole-text search (C speed); only a file with a raw match pays for masking and line splitting.
            if not rx.search(text):
                continue
            if masked is None:
                masked = mask_strings(text) if path.suffix == ".py" else text
                lines = text.split(NL)
                mlines = masked.split(NL)
            for m in rx.finditer(masked):
                n = masked.count(NL, 0, m.start()) + 1
                line = lines[n - 1]
                col = m.start() - (masked.rfind(NL, 0, m.start()) + 1)
                if line.lstrip().startswith(("#", "{#")) or _exempt(lines, mlines, n - 1, col):
                    continue
                bucket = hits[(e["id"], p["id"])].setdefault(rel, [])
                if n not in bucket:
                    bucket.append(n)
    return hits


def counts_of(hits) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for (eid, pid), files in sorted(hits.items()):
        if files:
            out[f"{eid}/{pid}"] = {f: len(lines) for f, lines in sorted(files.items())}
    return out


def load_baseline(root: Path, baseline: Path | None = None) -> dict:
    path = baseline or (root / BASELINE)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("patterns", {})


def excess_of(root: Path, files: dict, base: dict) -> dict[str, int]:
    """{file: hits above its own baseline} for one pattern, or {} when the excess is only a move.

    The per-file baseline is the rule. Excess is forgiven only up to the baselined hits of files that no
    longer exist (a rename), so an in-place cleanup elsewhere never makes room for a new copy."""
    growth = {rel: len(v) - base.get(rel, 0) for rel, v in files.items() if len(v) > base.get(rel, 0)}
    moved = sum(n for rel, n in base.items() if not (root / rel).exists())
    return {} if sum(growth.values()) <= moved else growth


def compare(entries, hits, baseline, root: Path) -> tuple[list[str], list[str]]:
    """(violations, slack notes) for rule (b) and (c)."""
    by_id = {e["id"]: e for e in entries}
    fails, slack = [], []
    for (eid, pid), files in sorted(hits.items()):
        base = baseline.get(f"{eid}/{pid}", {})
        e = by_id[eid]
        growth = excess_of(root, files, base)
        if not growth:
            continue
        where = ", ".join(f"{rel}:{n}" for rel in sorted(growth) for n in sorted(files[rel])[-growth[rel]:])
        canon = e.get("canonical") or {}
        fails.append(
            f"[{eid}/{pid}] new or grown in: {where} (baseline allows {sum(base.values())} in total, per file).\n"
            f"      concept: {e['concept']}\n"
            f"      reuse:   {str(e.get('reuse', '')).strip()}\n"
            f"      where:   {canon.get('path', '?')} ({', '.join(canon.get('symbols') or [])})\n"
            f"      If this is genuinely not a second copy, add a known_exceptions entry with a "
            f"reason to docs/reuse-register.yml, or `# reuse-ok: <reason>` on or just above the line."
        )
    current = counts_of(hits)
    for key, files in sorted(baseline.items()):
        for rel, n in sorted(files.items()):
            now = current.get(key, {}).get(rel, 0)
            if now < n:
                slack.append(f"baseline {key} {rel}: {n} -> {now} (run --update-baseline to lower)")
    return fails, slack


# ---------------------------------------------------------------- baseline update


def update_baseline(root: Path, entries, baseline_path: Path | None = None):
    """(ok, messages). Writes the current counts only if none rises and no file is added."""
    path = baseline_path or (root / BASELINE)
    current = counts_of(find_hits(root, entries))
    old = load_baseline(root, path) if path.exists() else None
    messages = []
    if old is not None:
        for key, files in current.items():
            grown = excess_of(root, {r: [0] * n for r, n in files.items()}, old.get(key, {}))
            if grown:
                messages.append(f"refused: {key} would rise in {', '.join(sorted(grown))}")
        if messages:
            messages.append("The baseline only falls. Reuse the canonical implementation, or add a "
                            "known_exceptions entry with a reason to the register.")
            return False, messages
    for key, files in (old or {}).items():
        for rel, n in files.items():
            now = current.get(key, {}).get(rel, 0)
            if now < n:
                messages.append(f"lowered {key} {rel}: {n} -> {now}")
    path.write_text(json.dumps({
        "_comment": ("Hits of a register must_not_create pattern that already existed, per pattern and "
                     "file. Written by scripts/check_reuse_register.py --update-baseline, which "
                     "refuses to raise a count or add a file. Lowering is routine."),
        "patterns": current,
    }, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return True, messages or ["baseline written"]


# ---------------------------------------------------------------- run


def run(root: Path, register: Path | None = None, baseline: Path | None = None):
    """(failures, notes). Failures are everything that must stop the build."""
    entries, problems = load_register(root, register)
    if problems:
        return problems, []
    fails, notes = check_canonicals(root, entries)
    hits = find_hits(root, entries)
    more, slack = compare(entries, hits, load_baseline(root, baseline), root)
    return fails + more, notes + slack


def print_entries(entries) -> None:
    for e in entries:
        c = e.get("canonical") or {}
        print(f"{e['id']:26s} [{e['status']}] {e['concept']}")
        if e["status"] == "undetermined":
            print(f"    candidates: {'; '.join(map(str, e.get('candidates') or []))}")
        else:
            print(f"    use: {c.get('path')} :: {', '.join(c.get('symbols') or [])}")
            print(f"    {str(e.get('reuse', '')).strip()}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=REPO)
    parser.add_argument("--count", action="store_true")
    parser.add_argument("--entries", action="store_true")
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.entries or args.update_baseline:
        entries, problems = load_register(root)
        if problems:
            print("\n".join(problems))
            return 1
        if args.entries:
            print_entries(entries)
            return 0
        ok, messages = update_baseline(root, entries)
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
    print(f"\nreuse register: {len(fails)} violation(s)")
    if fails:
        print("The register lists the one implementation to use for each concept: "
              "python scripts/check_reuse_register.py --entries")
    print(len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
