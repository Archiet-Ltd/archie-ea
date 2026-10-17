#!/usr/bin/env python
"""Sentence-level guardrails for public marketing copy, enforced as a ratchet.

    python scripts/check_marketing_copy.py                  # check (exit 1 on a violation)
    python scripts/check_marketing_copy.py --count          # violation count only
    python scripts/check_marketing_copy.py --claims         # list every absolute-claim sentence
    python scripts/check_marketing_copy.py --update-baseline  # lower the baseline; never raises it

Why
---
Independent review found the same two defects twice in one evening: roadmap language
("Q1 2027", "opens in", "Next:") surviving a fix round on a page that sells the product as it
is today, and an unqualified "every ..." claim replacing one overclaim with another. Both are
greppable. This script is the grep, run on every build, instead of a reviewer re-deriving the
pattern by hand.

What it checks
--------------
Hard-fail guardrails (a hit above the per-file baseline fails the build):

* ``roadmap-language`` -- ``Q[1-4] ?20dd``, ``opens in``, ``arrive(s)``, ``Next:``, ``quarter``,
  ``roadmap``. Scope: every ``content/pages/**/*.md``, plus the public templates and the home
  template, where the same wording was found. The product's own "Roadmaps" module name is the
  one allowed use; the plural does not match ``\\broadmap\\b``, and link targets, ``href``/``src``
  values and Jinja expressions are masked so a slug such as ``/modules/roadmaps`` or
  ``url_for('roadmaps.index')`` is never a hit.
* ``generic-filler`` -- seamless, revolutionary, unlock, empower, game-changing, cutting-edge,
  best-in-class, world-class, next-generation. Scope: ``content/pages/**/*.md`` only (templates
  carry UI labels, where an "Unlock" button is a real verb, not marketing).

Citation-only (informational, never fails the build):

* ``absolute-claim`` -- every, all, always, never, completely, fully, entirely. Printed by
  ``--claims`` with each sentence and whether it carries a ``claim-cited: <route or code>``
  marker. The judgment: these words are mostly harmless ("every organisation's data stays
  separate", "all fields are optional"), and a regex cannot tell a claim about product behaviour
  from a security statement or ordinary English, so failing on each would train people to
  silence the gate. The rule that matters -- the claim must be checkable -- needs a person to
  check it, so the script's job is to put every such sentence in front of the reviewer, with the
  uncited ones separated out, and the PR description carries the citations.

The baseline
------------
``scripts/marketing_copy_baseline.json`` holds, per guardrail and file, the hits that already
existed. It uses the same ratchet as ``scripts/check_reuse_register.py`` (its ``excess_of`` is
imported, not copied): a file may hold no more hits than its own baseline, a cleanup elsewhere
never makes room for a new hit, and ``--update-baseline`` refuses to raise a count or add a file.

Exemption
---------
``copy-ok: <reason>`` on the matching line or the line above it. A bare marker does not exempt.

Static: reads source text only. No imports of the application, no database.

Proven-against: a ``Q1 2027`` added to a content page, and an "Unlock" added to one -- the gate
names the file, line and guardrail and fails. Pinned in tests/test_check_marketing_copy.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_reuse_register import excess_of  # noqa: E402  -- the one ratchet implementation

REPO = Path(__file__).resolve().parent.parent
BASELINE = Path("scripts") / "marketing_copy_baseline.json"

MD_SCOPE = ["content/pages/**/*.md"]
TEMPLATE_SCOPE = ["app/templates/**/public/**/*.html", "app/templates/main/index.html"]

MARKER = re.compile(r"copy-ok:\s*\S")
CITED = re.compile(r"claim-cited:\s*\S")

GUARDRAILS = {
    "roadmap-language": {
        "regex": re.compile(
            r"Q[1-4] ?20\d\d|\bopens in\b|\barrives?\b|\bNext:|\bquarter\b|\broadmap\b", re.IGNORECASE
        ),
        "scope": MD_SCOPE + TEMPLATE_SCOPE,
        "message": ("roadmap language on a page that sells or explains the product as it is today; "
                    "describe what it does now, or move the dated plan to the changelog"),
    },
    "generic-filler": {
        "regex": re.compile(
            r"\b(seamless(ly)?|revolutionary|unlock(s|ed|ing)?|empower(s|ed|ing)?|game-changing|"
            r"cutting-edge|best-in-class|world-class|next-generation)\b",
            re.IGNORECASE,
        ),
        "scope": MD_SCOPE,
        "message": "generic filler with no checkable claim; name the specific thing the product does",
    },
}
ABSOLUTE = re.compile(r"\b(every|all|always|never|completely|fully|entirely)\b", re.IGNORECASE)

_MASKS = [
    re.compile(r"\]\([^)]*\)"),  # markdown link target
    re.compile(r"""\b(?:href|src|action|data-[\w-]+)\s*=\s*(?:"[^"]*"|'[^']*')"""),
    re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL),  # Jinja expressions and tags
    re.compile(r"`[^`\n]*`"),  # inline code
]


def _mask(text: str) -> str:
    """Blank (same length, newlines kept) everything that is a target or code, not prose."""
    for rx in _MASKS:
        text = rx.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    return text


def _split_front_matter(text: str) -> tuple[str, str]:
    """(front matter with its keys blanked to the value side, body). Keys are metadata, not copy."""
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    if end < 0:
        return "", text
    return text[: end + 4], text[end + 4:]


def _prose(path: Path, text: str) -> str:
    """The text a reader can see, line-aligned with the file. Only title/description-like front
    matter values are copy; slugs, urls and verification notes are not rendered."""
    masked = _mask(text)
    if path.suffix != ".md":
        return masked
    front, _ = _split_front_matter(masked)
    lines = masked.split("\n")
    n_front = front.count("\n") + 1 if front else 0
    visible = ("title:", "description:", "h1:", "summary:", "lede:")
    for i in range(min(n_front, len(lines))):
        if not lines[i].lstrip().lower().startswith(visible):
            lines[i] = ""
    return "\n".join(lines)


def _files(root: Path, scopes: list[str]) -> list[Path]:
    seen: dict[Path, None] = {}
    for scope in scopes:
        for p in sorted(root.glob(scope)):
            if p.is_file():
                seen[p] = None
    return list(seen)


def _exempt(raw_lines: list[str], idx: int) -> bool:
    return bool(MARKER.search(raw_lines[idx]) or (idx and MARKER.search(raw_lines[idx - 1])))


def find_hits(root: Path) -> dict[str, dict[str, list[int]]]:
    """{guardrail: {relative file: [1-based line numbers]}} for the hard-fail guardrails."""
    out: dict[str, dict[str, list[int]]] = {}
    for gid, g in GUARDRAILS.items():
        for path in _files(root, g["scope"]):
            text = path.read_text(encoding="utf-8", errors="replace")
            raw = text.split("\n")
            for idx, line in enumerate(_prose(path, text).split("\n")):
                if not g["regex"].search(line) or _exempt(raw, idx):
                    continue
                rel = path.relative_to(root).as_posix()
                out.setdefault(gid, {}).setdefault(rel, []).extend(
                    [idx + 1] * len(g["regex"].findall(line))
                )
    return out


def counts_of(hits) -> dict[str, dict[str, int]]:
    return {gid: {f: len(v) for f, v in sorted(files.items())} for gid, files in sorted(hits.items()) if files}


def find_claims(root: Path) -> list[tuple[str, int, str, bool]]:
    """Every sentence in a content page using an absolute word: (file, line, text, cited)."""
    out = []
    for path in _files(root, MD_SCOPE):
        text = path.read_text(encoding="utf-8", errors="replace")
        raw = text.split("\n")
        for idx, line in enumerate(_prose(path, text).split("\n")):
            if ABSOLUTE.search(line):
                cited = bool(CITED.search(raw[idx]) or (idx and CITED.search(raw[idx - 1])))
                out.append((path.relative_to(root).as_posix(), idx + 1, raw[idx].strip(), cited))
    return out


def load_baseline(root: Path, baseline: Path | None = None) -> dict:
    path = baseline or (root / BASELINE)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("guardrails", {})


def compare(root: Path, hits, baseline) -> tuple[list[str], list[str]]:
    """(violations, slack notes)."""
    fails, slack = [], []
    for gid, files in sorted(hits.items()):
        base = baseline.get(gid, {})
        growth = excess_of(root, files, base)
        if not growth:
            continue
        where = ", ".join(f"{rel}:{n}" for rel in sorted(growth) for n in sorted(files[rel])[-growth[rel]:])
        fails.append(
            f"[{gid}] new or grown in: {where} (baseline allows {sum(base.values())} in total, per file).\n"
            f"      {GUARDRAILS[gid]['message']}\n"
            f"      If this is genuinely fine, put `copy-ok: <reason>` on or just above the line."
        )
    current = counts_of(hits)
    for gid, files in sorted(baseline.items()):
        for rel, n in sorted(files.items()):
            now = current.get(gid, {}).get(rel, 0)
            if now < n:
                slack.append(f"baseline {gid} {rel}: {n} -> {now} (run --update-baseline to lower)")
    return fails, slack


def update_baseline(root: Path, baseline_path: Path | None = None):
    """(ok, messages). Writes the current counts only if none rises and no file is added."""
    path = baseline_path or (root / BASELINE)
    current = counts_of(find_hits(root))
    old = load_baseline(root, path) if path.exists() else None
    messages = []
    if old is not None:
        for gid, files in current.items():
            grown = excess_of(root, {r: [0] * n for r, n in files.items()}, old.get(gid, {}))
            if grown:
                messages.append(f"refused: {gid} would rise in {', '.join(sorted(grown))}")
        if messages:
            messages.append("The baseline only falls. Rewrite the copy, or mark a line `copy-ok: <reason>`.")
            return False, messages
    for gid, files in (old or {}).items():
        for rel, n in files.items():
            now = current.get(gid, {}).get(rel, 0)
            if now < n:
                messages.append(f"lowered {gid} {rel}: {n} -> {now}")
    path.write_text(json.dumps({
        "_comment": ("Hard-fail marketing-copy hits that already existed, per guardrail and file. Written "
                     "by scripts/check_marketing_copy.py --update-baseline, which refuses to raise a count "
                     "or add a file. Lowering is routine."),
        "guardrails": current,
    }, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return True, messages or ["baseline written"]


def run(root: Path, baseline: Path | None = None):
    fails, slack = compare(root, find_hits(root), load_baseline(root, baseline))
    return fails, slack


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=REPO)
    parser.add_argument("--count", action="store_true")
    parser.add_argument("--claims", action="store_true")
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.update_baseline:
        ok, messages = update_baseline(root)
        print("\n".join(messages))
        return 0 if ok else 1
    if args.claims:
        claims = find_claims(root)
        for rel, n, text, cited in claims:
            print(f"{'cited  ' if cited else 'UNCITED'} {rel}:{n}: {text}")
        print(f"\n{len(claims)} absolute-claim line(s), {sum(1 for c in claims if not c[3])} without a claim-cited marker "
              "(informational; checked by the reviewer, never fails the build)")
        return 0
    fails, slack = run(root)
    if args.count:
        print(len(fails))
        return 1 if fails else 0
    for s in slack:
        print(f"  note: {s}")
    for f in fails:
        print(f"  {f}")
    print(f"\nmarketing copy: {len(fails)} violation(s)")
    print(len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
