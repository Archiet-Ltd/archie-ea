#!/usr/bin/env python
"""Every route family that reads organisation data must have a two-organisation test.

A tenant boundary is only real if something crosses it and is turned away. A page
can be login-gated, carry the right role check and still read another
organisation's rows, because the query forgot its tenant filter. A status check
or a single-organisation test never notices: the one organisation it knows about
sees exactly what it expects. The failure only shows when a *second* organisation
exists and its data stays invisible.

So this gate groups the route inventory by blueprint into families, keeps the
families that read organisation data, and fails when such a family has no
two-organisation test associated with it.

Reads-organisation-data predicate: a family reads organisation data when at least
one of its routes has ``login_required`` true in docs/component-inventory.json. A
logged-in surface is a tenant-scoped surface; a route with no login requirement
serves the same bytes to everyone and has no tenant boundary to cross.

Two-organisation tests are found by STATIC AST search (ast.parse, no import, no
database, no Flask boot) across tests/ and app/modules/*/tests/. A test file
counts as a two-organisation test when a static scan of its functions and
fixtures shows a genuine SECOND-organisation signal:

* it defines or uses one of the shared two-organisation fixtures -- ``two_orgs``,
  ``two_organisations``, ``second_org_membership``;
* a function or fixture requests both of a dual-organisation pair -- ``org_a``
  and ``org_b`` together (the tenant-isolation test convention); or
* it defines or requests a fixture whose name names a second organisation --
  ``other_org``, ``second_org``, ``org_b``, ``org_two`` (as a whole word).

That last clause is why the authorisation matrix qualifies: it builds an
``other_org`` initiative to prove a cross-organisation 404. There is no
unconditional file-name allow-list -- a named suite that stopped building a
second organisation would stop counting, which is what makes the red proof
below bite.

ASSOCIATION is family-targeted, not a loose keyword match. A two-organisation
test covers a family only when the test source actually exercises that family's
routes:

* one of the blueprint's own route rules, taken from the inventory and reduced
  to its static prefix (the part before the first ``<id>`` segment), appears in
  the source -- e.g. ``/admin/billing``, ``/admin/team``, ``/intelligence/ask``,
  ``/applications/``, ``/account/manage``; or
* the blueprint's endpoint appears in a url_for-style string, ``"blueprint.view"``.

Bare English words (``billing``, ``account``, ``application``) are NOT matched,
and neither is a bare parent like ``/intelligence/``: they collide with ordinary
test content -- a ``business_capability="Billing"`` value, an unrelated
``/intelligence/`` string, a ``read_only_account`` parameter -- none of which is
a cross-organisation test of that family. Only a concrete route of the family
(``/intelligence/ask``) counts. A "covered" family therefore reflects a real,
removable two-organisation test that drives that family's own routes.

Startup families are measured at the family level: the seven startup families
(account, admin connectors, intelligence, applications, admin billing, admin
team, dashboard) each own one or more blueprints, and a family is covered when
at least one of its blueprints has a family-targeted two-organisation test.
These seven must always be zero uncovered.

A family is uncovered when it reads organisation data AND no two-organisation
test is associated with it AND no line carries a VALID escape hatch naming the
family. The escape hatch is ``two-org-coverage-ok: <family>[, <family> ...]
<reason including a brief id>`` and is accepted only when it carries both a
free-text reason AND a brief id token (``T-[a-z]+-\\d+``). A startup family can
NEVER be excused: the hatch cannot stand in for a real startup two-organisation
test, so a startup family named in a marker is ignored.

Baseline that only falls: scripts/ci/two_org_baseline.json records the accepted
uncovered count for NON-startup families. The gate fails when the non-startup
uncovered count rises above that baseline, and it always requires the startup
families to be zero uncovered regardless of the baseline. Lower the baseline when
you cover a family; never raise it.

    python scripts/check_two_org_coverage.py            # list uncovered families
    python scripts/check_two_org_coverage.py --count    # trailing line = count
    python scripts/check_two_org_coverage.py --startup-only  # startup families only

Proven-against: on a scratch edit, rename the authorisation matrix's second
organisation away -- in tests/smoke/test_authorisation_matrix.py replace the
``other_org`` fixture and its references (so the file no longer builds a second
organisation) -- and run ``python scripts/check_two_org_coverage.py``: the
``admin billing`` startup family, which only that suite covers through its
``/admin/billing`` rows, goes uncovered, the startup count rises above 0 and the
gate exits 1. Restore the file with ``git checkout --`` and it is green at 0
startup uncovered again.
"""
from __future__ import annotations

import argparse
import ast
import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INVENTORY = os.path.join("docs", "component-inventory.json")
BASELINE = os.path.join("scripts", "ci", "two_org_baseline.json")

# Shared fixtures that, on their own, mark a test as a two-organisation test.
TWO_ORG_NAMES = ("two_orgs", "two_organisations", "second_org_membership")

# Dual-organisation fixture pairs: a function that requests both members of a
# pair is a two-organisation test (the tenant-isolation convention).
DUAL_ORG_PAIRS = ({"org_a", "org_b"}, {"org1", "org2"}, {"org_one", "org_two"})

# A fixture or function whose name names a SECOND organisation. Matched as a
# whole word so a `portfolio` or a `storage` identifier never trips it.
SECOND_ORG_NAME = re.compile(r"(?:^|_)(other_org|second_org|org_b|org_two)(?:_|$)")

ALLOW = re.compile(r"two-org-coverage-ok:[ \t]*\S")

# A brief id token, e.g. T-qa-13. An escape-hatch excuse must carry one so an
# accepted gap always points at the work that owns it. Same shape as
# check_asvs_register.BRIEF_ID -- one grammar for a brief id across both gates.
BRIEF_ID = re.compile(r"T-[a-z]+-\d+")

# The family list that an escape-hatch marker names, parsed from exactly the
# text after the colon and NEVER from the free-text reason that follows it.
# The grammar is one of:
#   two-org-coverage-ok: <name>[, <name> ...] [<free-text reason>]
#   two-org-coverage-ok: [<name>, <name> ...] [<free-text reason>]
# In the bare form the family names are the leading comma-separated identifier
# tokens; parsing stops at the first token that is not an identifier or a comma
# separator (so the reason prose after the names is never read as a family). In
# the bracketed form only the identifiers inside the first [...] are families.
_MARKER = re.compile(r"two-org-coverage-ok:[ \t]*(?P<rest>.*)$")
_BRACKET = re.compile(r"^\[(?P<body>[^\]]*)\]")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _leading_identifiers(text: str) -> set[str]:
    """The leading run of comma/whitespace-separated identifier tokens.

    Parsing stops at the first token that is not a bare identifier, so any
    free-text reason following the family list is never read as a family.
    """
    families, _reason = _split_families_reason(text)
    return families


def _split_families_reason(text: str) -> tuple[set[str], str]:
    """Split a bare family-list-and-reason string into (families, reason).

    The leading run of identifier tokens is the family list; everything from the
    first non-identifier token onward is the free-text reason. A brief id such
    as ``T-qa-13`` is not a bare identifier, so it always falls into the reason.
    """
    families: set[str] = set()
    tokens = re.split(r"(\s*,\s*|\s+)", text.strip())
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token.strip():  # a separator chunk
            i += 1
            continue
        if _IDENTIFIER.fullmatch(token):
            families.add(token)
            i += 1
            continue
        break
    reason = "".join(tokens[i:]).strip()
    return families, reason


def _marker_families(line: str) -> set[str]:
    """Family names a `two-org-coverage-ok:` marker on this line declares.

    Reads only the family list immediately after the colon -- either a bracketed
    ``[a, b]`` list or the leading comma-separated identifiers -- and stops at
    the first free-text word, so a reason like ``webhook is a public endpoint``
    yields only ``webhook``.
    """
    match = _MARKER.search(line)
    if not match:
        return set()
    rest = match.group("rest").strip()
    if not rest:
        return set()

    bracket = _BRACKET.match(rest)
    if bracket:
        return _leading_identifiers(bracket.group("body"))

    return _leading_identifiers(rest)


# The gates' own synthetic tests build fake route strings and fake second-org
# fixtures to exercise this script; they are not product two-organisation tests
# and must not count as coverage for any real family.
SELF_TESTS = frozenset(
    {
        os.path.join("tests", "test_check_two_org_coverage.py"),
        os.path.join("tests", "test_check_asvs_register.py"),
    }
)

# Startup families: English name -> the blueprint names it owns. The seven
# startup families must resolve to 0 uncovered at the family level, where a
# family is covered when at least one of its blueprints has a family-targeted
# two-organisation test. Association to a blueprint uses that blueprint's own
# route rules from the inventory, so no per-family keyword list is kept here.
STARTUP_FAMILIES: dict[str, set[str]] = {
    "account": {"account"},
    "admin connectors": {"m365_connector", "connectors"},
    "intelligence": {"intelligence_api", "intelligence_ui"},
    "applications": {"unified_applications", "application_mgmt", "my_applications"},
    "admin billing": {"billing"},
    "admin team": {"team"},
    "dashboard": {"dashboard", "dashboard_pages"},
}

# blueprint name -> startup family English name. Public API: the register gate
# reuses BLUEPRINT_TO_STARTUP so the startup-family mapping has one source of
# truth across both gates.
BLUEPRINT_TO_STARTUP: dict[str, str] = {}
for _fam, _bps in STARTUP_FAMILIES.items():
    for _bp in _bps:
        BLUEPRINT_TO_STARTUP[_bp] = _fam

# Backwards-compatible private alias kept for this module's own call sites.
_BLUEPRINT_TO_STARTUP = BLUEPRINT_TO_STARTUP

# The shortest static prefix of a route rule that is still distinctive enough to
# associate a test with a family: at least two path segments and six characters,
# so ``/ai`` or ``/`` never associates but ``/admin/team`` does.
_MIN_PREFIX_LEN = 6


def _static_prefix(rule: str) -> str:
    """The leading part of a rule before its first ``<id>`` segment."""
    segments: list[str] = []
    for segment in rule.split("/"):
        if segment.startswith("<"):
            break
        segments.append(segment)
    prefix = "/".join(segments)
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    prefix = prefix.rstrip("/")
    return prefix or "/"


def blueprint_route_prefixes(routes: list[dict]) -> dict[str, set[str]]:
    """blueprint name -> the set of distinctive static rule prefixes it owns."""
    prefixes: dict[str, set[str]] = {}
    for route in routes:
        blueprint = route.get("blueprint")
        if not blueprint or not route.get("login_required"):
            continue
        prefix = _static_prefix(route.get("rule") or "")
        if prefix.count("/") >= 2 and len(prefix) >= _MIN_PREFIX_LEN:
            prefixes.setdefault(blueprint, set()).add(prefix)
    return prefixes


class InventoryError(RuntimeError):
    """The component inventory is missing or cannot be parsed.

    Raised by :func:`load_routes` so a missing or corrupt
    docs/component-inventory.json is a loud gate failure with a plain message,
    never a silent empty list that passes every family.
    """


def load_routes(root: str) -> list[dict]:
    """Read the route list from the component inventory without importing anything.

    A missing or unparsable inventory is a GATE FAILURE, not a silent empty
    list: without the inventory the gate cannot see any family and would pass
    every tenant boundary by default. Raise :class:`InventoryError` with a plain
    message so the caller reports it and exits non-zero.
    """
    path = os.path.join(root, INVENTORY)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise InventoryError(
            f"{INVENTORY}: [two-org-coverage] the route inventory is missing; "
            f"the gate cannot measure any tenant boundary without it"
        ) from exc
    except (OSError, ValueError) as exc:
        raise InventoryError(
            f"{INVENTORY}: [two-org-coverage] the route inventory could not be "
            f"parsed ({exc}); the gate cannot measure any tenant boundary from a "
            f"broken inventory"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("routes"), list):
        raise InventoryError(
            f"{INVENTORY}: [two-org-coverage] the route inventory has no 'routes' "
            f"list; the gate cannot measure any tenant boundary from it"
        )
    return data.get("routes", [])


def families_reading_org_data(routes: list[dict]) -> set[str]:
    """Blueprints with at least one login-required route (a tenant-scoped surface)."""
    families: set[str] = set()
    for route in routes:
        blueprint = route.get("blueprint")
        if not blueprint:
            continue
        if route.get("login_required"):
            families.add(blueprint)
    return families


def _iter_test_files(root: str):
    """Yield (relpath, source) for every test file under tests/ and app/modules/*/tests/."""
    seen: set[str] = set()
    search_dirs = [os.path.join(root, "tests")]
    modules = os.path.join(root, "app", "modules")
    if os.path.isdir(modules):
        for module in sorted(os.listdir(modules)):
            tests_dir = os.path.join(modules, module, "tests")
            if os.path.isdir(tests_dir):
                search_dirs.append(tests_dir)

    for base in search_dirs:
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, filenames in os.walk(base):
            if "__pycache__" in dirpath:
                continue
            for filename in sorted(filenames):
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(dirpath, filename)
                rel = os.path.relpath(path, root)
                if rel in seen or rel in SELF_TESTS:
                    continue
                seen.add(rel)
                with open(path, encoding="utf-8", errors="replace") as fh:
                    yield rel, fh.read()


def _unit_is_two_org(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when this one function/fixture itself carries a second-org signal.

    The signal is the unit's own name naming a second organisation, or one of
    its parameters being a shared two-organisation fixture, a dual-org pair, or
    a second-org-named fixture. A bare reference buried in an expression (an
    attribute ``config.org_b``, a passing mention in a string) is never a
    signal, so a single-organisation test cannot be mistaken for a two-org one.
    """
    name = node.name
    if name in TWO_ORG_NAMES or SECOND_ORG_NAME.search(name):
        return True
    arg_names = {a.arg for a in node.args.args}
    if arg_names & set(TWO_ORG_NAMES):
        return True
    if any(pair <= arg_names for pair in DUAL_ORG_PAIRS):
        return True
    if any(SECOND_ORG_NAME.search(a) for a in arg_names):
        return True
    return False


def _is_two_org_source(source: str) -> bool:
    """True when a static scan shows a genuine second-organisation signal.

    Kept for the file-level question "does this file build a second
    organisation at all"; association is decided per unit by
    :func:`two_org_units` so a second-org signal in one function cannot vouch
    for an unrelated single-organisation test elsewhere in the same file.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _unit_is_two_org(node):
            return True
    return False


def _unit_source(source: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """The source text of a single function/fixture, used for route association."""
    try:
        return ast.get_source_segment(source, node) or ""
    except Exception:  # noqa: BLE001 - defensive; fall back to whole source
        return ""


def two_org_units(source: str) -> list[str]:
    """Source text of each test UNIT that is bound to a second organisation.

    A unit qualifies when it carries the second-org signal itself (a two-org
    fixture definition, a dual-org pair, a second-org-named fixture) OR when it
    requests, as a parameter, a fixture defined in the same file that itself
    carries the signal. This is what stops a file-level coincidence from
    counting: the ``other_org`` fixture that feeds the interface-register test
    does not lend its signal to an unrelated billing test that never requests
    it.

    Returns the source segment of each qualifying unit so association is checked
    against that unit's own body, not against any line anywhere in the file.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    # Names of same-file fixtures/functions that carry the signal directly.
    two_org_fixture_names = {node.name for node in functions if _unit_is_two_org(node)}

    units: list[str] = []
    for node in functions:
        bound = _unit_is_two_org(node)
        if not bound:
            arg_names = {a.arg for a in node.args.args}
            if arg_names & two_org_fixture_names:
                bound = True
        if bound:
            units.append(_unit_source(source, node))
    return units


def two_org_tests(root: str) -> list[tuple[str, list[str]]]:
    """Return (relpath, two-org unit sources) for every two-organisation test file.

    The second element is the list of per-unit source segments that are bound to
    a second organisation; association is checked against these units only.
    """
    found: list[tuple[str, list[str]]] = []
    for rel, source in _iter_test_files(root):
        units = two_org_units(source)
        if units:
            found.append((rel, units))
    return found


def _endpoint_in_string(blueprint: str, source: str) -> bool:
    """True when ``"blueprint.view"`` appears as a url_for-style string token."""
    pattern = r"""["']""" + re.escape(blueprint) + r"\.[A-Za-z_]"
    return re.search(pattern, source) is not None


def associates(blueprint: str, source: str, prefixes: set[str]) -> bool:
    """Family-targeted association for one blueprint against one test source.

    Covered when the source references the blueprint's endpoint in a
    url_for-style string, or names one of the blueprint's own route rule
    prefixes. Bare English words are never matched.

    Public API: :mod:`check_asvs_register` reuses this predicate so the
    "a two-organisation test drives this family's own route" rule has one
    implementation shared across both gates.
    """
    if _endpoint_in_string(blueprint, source):
        return True
    for prefix in prefixes:
        if prefix in source:
            return True
    return False


# Backwards-compatible private alias kept for this module's own call sites.
_associates = associates


def covered_families(
    org_families: set[str],
    tests: list[tuple[str, list[str]]],
    route_prefixes: dict[str, set[str]],
) -> set[str]:
    """Blueprints for which at least one two-organisation test UNIT is associated.

    Association is unit-level: the SAME unit that is bound to a second
    organisation must also reference the family's route. A two-org fixture in
    one function and a single-org reference to the family's route in a different
    function no longer combine into coverage.
    """
    covered: set[str] = set()
    unit_sources = [unit for _rel, units in tests for unit in units]
    for blueprint in org_families:
        prefixes = route_prefixes.get(blueprint, set())
        for unit in unit_sources:
            if _associates(blueprint, unit, prefixes):
                covered.add(blueprint)
                break
    return covered


def _excused_by_marker(line: str) -> set[str]:
    """Blueprints a single `two-org-coverage-ok:` line validly excuses.

    An excuse is accepted only when it carries BOTH a free-text reason after the
    family list AND a brief id token (``T-[a-z]+-\\d+``) in that reason. A marker
    that is nothing but a family name excuses nothing. A startup family can never
    be excused: any startup family named in the marker is dropped, so the escape
    hatch cannot stand in for a real startup two-organisation test.
    """
    match = _MARKER.search(line)
    if not match:
        return set()
    rest = match.group("rest").strip()
    if not rest:
        return set()

    bracket = _BRACKET.match(rest)
    if bracket:
        families = _leading_identifiers(bracket.group("body"))
        reason = rest[bracket.end():].strip()
    else:
        families, reason = _split_families_reason(rest)

    if not families:
        return set()
    # The excuse needs a free-text reason that carries a brief id.
    if not reason or not BRIEF_ID.search(reason):
        return set()

    # Startup families can never be excused by the hatch.
    return {fam for fam in families if fam not in _BLUEPRINT_TO_STARTUP}


def excused_families(root: str) -> set[str]:
    """Blueprints excused by a valid `two-org-coverage-ok:` line anywhere in tests."""
    excused: set[str] = set()
    for _rel, source in _iter_test_files(root):
        for line in source.split("\n"):
            if ALLOW.search(line):
                excused |= _excused_by_marker(line)
    return excused


def uncovered(root: str) -> tuple[list[str], list[str]]:
    """Return (uncovered startup families, uncovered non-startup blueprints), sorted.

    Startup coverage is measured at the FAMILY level: a startup family is
    uncovered only when none of its blueprints is covered or excused. The first
    list therefore holds startup family English names; the second holds
    non-startup blueprint names.
    """
    routes = load_routes(root)
    org_families = families_reading_org_data(routes)
    route_prefixes = blueprint_route_prefixes(routes)
    tests = two_org_tests(root)
    covered = covered_families(org_families, tests, route_prefixes)
    excused = excused_families(root)

    def ok(blueprint: str) -> bool:
        return blueprint in covered or blueprint in excused

    startup_missing = sorted(
        family
        for family, blueprints in STARTUP_FAMILIES.items()
        # a startup family is present only if at least one of its blueprints
        # actually reads organisation data; if none do, it is not in scope
        if (blueprints & org_families)
        and not any(ok(bp) for bp in (blueprints & org_families))
    )

    other_missing = sorted(
        bp
        for bp in org_families
        if bp not in _BLUEPRINT_TO_STARTUP and not ok(bp)
    )
    return startup_missing, other_missing


class BaselineError(RuntimeError):
    """scripts/ci/two_org_baseline.json is missing or malformed."""


def _parse_date(value: str) -> "datetime.date | None":
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def load_baseline(root: str) -> list[dict]:
    """Read the dated accepted-uncovered entries from the baseline file.

    The schema is a list of ``{family, brief, expiry}`` entries under
    ``accepted_uncovered`` (see the file's own ``_comment``). A missing file is
    an empty accepted set (nothing is excused). A present-but-malformed file is
    a :class:`BaselineError` so the ratchet can never be silently disabled.
    """
    path = os.path.join(root, BASELINE)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise BaselineError(
            f"{BASELINE}: [two-org-coverage] the baseline could not be parsed "
            f"({exc}); the ratchet cannot run"
        ) from exc

    accepted = data.get("accepted_uncovered", [])
    if not isinstance(accepted, list):
        raise BaselineError(
            f"{BASELINE}: [two-org-coverage] 'accepted_uncovered' must be a list "
            f"of {{family, brief, expiry}} entries (the bare-integer schema is "
            f"no longer accepted)"
        )
    entries: list[dict] = []
    for entry in accepted:
        if not isinstance(entry, dict):
            raise BaselineError(
                f"{BASELINE}: [two-org-coverage] every accepted entry must be a "
                f"mapping with family, brief and expiry; got {entry!r}"
            )
        family = str(entry.get("family", "")).strip()
        brief = str(entry.get("brief", "")).strip()
        expiry = str(entry.get("expiry", "")).strip()
        if not family or not BRIEF_ID.search(brief) or _parse_date(expiry) is None:
            raise BaselineError(
                f"{BASELINE}: [two-org-coverage] accepted entry for "
                f"{family or '?'!r} must name a family, a brief id (T-area-n) and "
                f"an ISO expiry date (YYYY-MM-DD); got "
                f"family={family!r} brief={brief!r} expiry={expiry!r}"
            )
        entries.append({"family": family, "brief": brief, "expiry": expiry})
    return entries


class Verdict:
    """The single pass/fail decision for two-organisation coverage.

    Holds everything both callers need: the two measured counts, the overall
    verdict, and the plain detail messages (including the dated-baseline rule).
    One object so the script's exit code and verify.py's gate agree by
    construction -- there is no second copy of the arithmetic.
    """

    def __init__(
        self,
        startup_missing: list[str],
        other_missing: list[str],
        ok: bool,
        messages: list[str],
    ) -> None:
        self.startup_missing = startup_missing
        self.other_missing = other_missing
        self.ok = ok
        self.messages = messages

    @property
    def startup_count(self) -> int:
        return len(self.startup_missing)

    @property
    def other_count(self) -> int:
        return len(self.other_missing)


def evaluate(root: str, today: "datetime.date | None" = None) -> Verdict:
    """Decide two-organisation coverage for a tree. The one public decision.

    Rules enforced here (and nowhere else):

    * Startup families must be 0 uncovered; a startup family can never be
      excused by a baseline entry.
    * For non-startup families, the measured uncovered set must EQUAL the set of
      still-valid (not past expiry) accepted families:
        - a measured-uncovered family with NO accepted entry fails;
        - an accepted entry whose family is NOT measured-uncovered fails (the
          ratchet only falls: a family that gained coverage must drop its
          entry);
        - an accepted entry PAST its expiry fails, naming the family, its brief
          and its expiry date.
    * A missing/broken inventory or a malformed baseline fails loudly (raised by
      the loaders).
    """
    today = today or datetime.date.today()
    startup_missing, other_missing = uncovered(root)
    accepted = load_baseline(root)

    measured = set(other_missing)
    valid_families: set[str] = set()
    messages: list[str] = []

    for entry in accepted:
        family, brief, expiry = entry["family"], entry["brief"], entry["expiry"]
        expiry_date = _parse_date(expiry)
        if family in STARTUP_FAMILIES or family in BLUEPRINT_TO_STARTUP:
            messages.append(
                f"{BASELINE}: [two-org-coverage] startup family {family!r} can "
                f"never be excused by a baseline entry (brief {brief}, expiry "
                f"{expiry}); it must carry a real two-organisation test"
            )
            continue
        if expiry_date is not None and expiry_date < today:
            messages.append(
                f"{BASELINE}: [two-org-coverage] accepted family {family!r} is "
                f"past its expiry {expiry} (brief {brief}); cover it or renew the "
                f"entry with a new brief and date"
            )
            continue
        valid_families.add(family)

    unaccounted = sorted(measured - valid_families)
    for family in unaccounted:
        messages.append(
            f"{BASELINE}: [two-org-coverage] family {family!r} is uncovered but "
            f"has no valid accepted entry; add a two-organisation test or a dated "
            f"{{family, brief, expiry}} entry naming the brief that will cover it"
        )

    stale = sorted(valid_families - measured)
    for family in stale:
        messages.append(
            f"{BASELINE}: [two-org-coverage] accepted family {family!r} is no "
            f"longer uncovered; remove its entry (the accepted set can only "
            f"shrink)"
        )

    if startup_missing:
        messages.append(
            f"Startup families must be 0 uncovered; "
            f"{len(startup_missing)} are uncovered: {', '.join(startup_missing)}."
        )

    ok = not startup_missing and measured == valid_families
    return Verdict(startup_missing, other_missing, ok, messages)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", action="store_true", help="print only the count")
    parser.add_argument("--root", default=ROOT, help="tree to scan")
    parser.add_argument(
        "--startup-only",
        action="store_true",
        help="consider only the startup families",
    )
    args = parser.parse_args()

    root = os.path.abspath(args.root)

    try:
        verdict = evaluate(root)
    except (InventoryError, BaselineError) as exc:
        # A missing/broken inventory or a malformed baseline is a loud failure,
        # never a silent empty pass.
        print(str(exc))
        print(1)
        return 1

    startup_missing = verdict.startup_missing
    other_missing = verdict.other_missing

    if args.startup_only:
        reported = list(startup_missing)
    else:
        reported = startup_missing + other_missing

    if not args.count:
        for name in reported:
            if name in STARTUP_FAMILIES:
                print(
                    f"docs/component-inventory.json: [two-org-coverage] startup "
                    f"family {name!r} reads organisation data but has no "
                    f"family-targeted two-organisation test"
                )
            else:
                print(
                    f"docs/component-inventory.json: [two-org-coverage] route "
                    f"family {name!r} reads organisation data but has no "
                    f"two-organisation test"
                )
        if reported:
            print()
            print(
                "A route family that reads organisation data with no "
                "two-organisation\ntest is an untested isolation boundary. Add a "
                "test under tests/ or\napp/modules/*/tests/ that creates two "
                "organisations (use the two_orgs or\ntwo_organisations fixture, or "
                "an org_a/org_b pair) and drives one of that\nfamily's own routes, "
                "asserting one organisation cannot see the other's\nrows. If the "
                "family genuinely has no tenant boundary, append\n"
                "'two-org-coverage-ok: <family> <reason including a brief id like "
                "T-qa-13>'\nnaming it (a startup family can never be excused)."
            )

    # The full (non-startup-only) gate obeys the dated-baseline decision.
    if not args.startup_only and not args.count and not verdict.ok:
        print()
        for message in verdict.messages:
            print(message)

    print(len(reported))

    if args.startup_only:
        return 1 if startup_missing else 0
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    sys.exit(main())
