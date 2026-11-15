"""Tests for the plain-terms sentence on derived rows (FR-15).

Covers the three confidence bands and their boundaries, the rounding of the
percentage, the omission of the confidence clause when confidence is null, the
absence of a sentence when a name, a depth or a relationship type is missing,
which element is named first and which wording is used for every derived
relationship type, "1 hop" versus "N hops", the sentence as read through
``cross_layer_impact`` from either end of a fact, and the scan that keeps the
sentence's wording in ``plain_terms.py`` alone.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.modules.intelligence.services.plain_terms import (
    SUPPORTED_TYPES,
    link_sentence,
    plain_terms_sentence,
    wording_family,
)

# Fixtures (app, db_session, make_org, ...) come from this directory's conftest.py.

HEAD = "We worked this out because Billing depends on Ledger, 2 hops away."


def _sentence(confidence, *, source="Ledger", target="Billing", type_="Serving", depth=2):
    """Serving: the stored TARGET (Billing) depends on the stored SOURCE (Ledger)."""
    return plain_terms_sentence(
        source_name=source, target_name=target, relation_type=type_, depth=depth, confidence=confidence
    )


# --- one drawn link ---------------------------------------------------------


@pytest.mark.parametrize("stored", ["Serving", "serving", "ServingRelationship"])
def test_a_link_reads_the_same_whatever_spelling_it_was_stored_in(stored):
    assert link_sentence(source_name="Ledger", target_name="Billing", relation_type=stored) == (
        "Billing depends on Ledger."
    )


@pytest.mark.parametrize("stored", [None, "", "pairs-with"])
def test_a_link_with_no_wording_still_reads_as_a_sentence_never_a_missing_value(stored):
    sentence = link_sentence(source_name="Ledger", target_name="Billing", relation_type=stored)
    assert sentence == "Ledger and Billing are linked."


# --- the shape and the three bands ------------------------------------------


def test_a_serving_fact_reads_as_one_full_sentence_with_a_confidence_clause():
    assert _sentence(0.82) == (
        "We worked this out because Billing depends on Ledger, 2 hops away. Fairly confident (82%)."
    )


@pytest.mark.parametrize(
    "confidence, clause",
    [
        (1.0, "Very confident (100%)."),
        (0.95, "Very confident (95%)."),
        (0.90, "Very confident (90%)."),  # boundary: >= 0.90 is the top band
        (0.85, "Fairly confident (85%)."),
        (0.70, "Fairly confident (70%)."),  # boundary: >= 0.70 is the middle band
        (0.69, "Worth a second look — we are less confident (69%)."),  # boundary: just under
        (0.01, "Worth a second look — we are less confident (1%)."),
        (Decimal("0.90"), "Very confident (90%)."),  # what the store's Numeric(3,2) hands back
        (Decimal("0.70"), "Fairly confident (70%)."),
        (Decimal("0.69"), "Worth a second look — we are less confident (69%)."),
    ],
)
def test_confidence_bands_at_the_boundaries(confidence, clause):
    assert _sentence(confidence) == f"{HEAD} {clause}"


@pytest.mark.parametrize(
    "confidence, pct",
    [(0.825, 83), (0.835, 84), (0.845, 85), (0.005, 1), (0.994, 99), (0.996, 100)],
)
def test_percent_is_confidence_rounded_half_up_to_a_whole_number(confidence, pct):
    # Half-up, not banker's rounding, and not at the mercy of binary floats:
    # 0.845 * 100 is 84.49999999999999 as a float, yet reads as 85%.
    assert f"({pct}%)." in _sentence(confidence)


# --- null confidence and absent names ---------------------------------------


def test_null_confidence_omits_the_clause_and_never_prints_zero_percent():
    sentence = _sentence(None)
    assert sentence == HEAD
    assert "%" not in sentence
    assert "0%" not in sentence
    assert "confident" not in sentence.lower()


def test_measured_zero_confidence_is_shown_as_zero_and_is_not_null():
    """A real 0.0 is a measurement and is rendered; only ``None`` (not
    recorded) is omitted -- the two are never conflated."""
    assert _sentence(0.0) == f"{HEAD} Worth a second look — we are less confident (0%)."
    assert _sentence(0) != _sentence(None)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target": None},
        {"source": None},
        {"target": None, "source": None},
        {"target": ""},
        {"source": "   "},
        {"depth": None},
    ],
)
def test_absent_name_or_depth_makes_the_whole_sentence_null(kwargs):
    # Never a sentence with a gap in it.
    assert _sentence(0.82, **kwargs) is None
    assert _sentence(None, **kwargs) is None


def test_a_confidence_that_is_not_a_number_is_a_loud_error():
    with pytest.raises(TypeError):
        _sentence("high")
    with pytest.raises(TypeError):
        _sentence(True)


# --- the sentence never reverses the relationship ---------------------------

SRC = "SourceEl"
TGT = "TargetEl"

# The expectation table is written here from ArchiMate's own meaning of each
# relationship, NOT read back from the module under test, so a wrong direction
# in the module cannot agree with itself.
#   (derived type, stored end named FIRST, wording family, the sentence at
#    depth 2 with no confidence)
EXPECTED = (
    ("Serving", "target", "dependency", f"We worked this out because {TGT} depends on {SRC}, 2 hops away."),
    ("Realization", "target", "dependency", f"We worked this out because {TGT} depends on {SRC}, 2 hops away."),
    ("Assignment", "target", "dependency", f"We worked this out because {TGT} depends on {SRC}, 2 hops away."),
    ("Triggering", "target", "dependency", f"We worked this out because {TGT} depends on {SRC}, 2 hops away."),
    ("Flow", "target", "dependency", f"We worked this out because {TGT} depends on {SRC}, 2 hops away."),
    ("Access", "source", "access", f"We worked this out because {SRC} accesses {TGT}, 2 hops away."),
    ("Composition", "source", "whole-part", f"We worked this out because {SRC} is made up of {TGT}, 2 hops away."),
    ("Aggregation", "source", "whole-part", f"We worked this out because {SRC} includes {TGT}, 2 hops away."),
    (
        "Specialization",
        "source",
        "general-specific",
        f"We worked this out because {SRC} is a specific kind of {TGT}, 2 hops away.",
    ),
    ("Association", "source", "symmetric", f"We worked this out because {SRC} and {TGT} are linked, 2 hops away."),
    ("Influence", "source", "influence", f"We worked this out because {SRC} influences {TGT}, 2 hops away."),
)
EXPECTED_BY_TYPE = {row[0]: row for row in EXPECTED}
DEPENDENCY_TYPES = {"Serving", "Realization", "Assignment", "Triggering", "Flow"}


def test_every_derived_type_has_wording_and_no_type_is_missing():
    """The eleven types are exactly the ones the derivation engine can store
    (every member of ``STRENGTH_ORDER`` is reachable as a ``derived_type``: a
    transparent link passes the other link's type through). A type added to the
    engine without wording here -- or wording for a type it cannot emit -- is red.
    """
    from app.services.archimate_derivation_service import STRENGTH_ORDER

    assert len(EXPECTED) == len(EXPECTED_BY_TYPE) == 11
    assert set(EXPECTED_BY_TYPE) == set(STRENGTH_ORDER)
    assert SUPPORTED_TYPES == set(STRENGTH_ORDER)


@pytest.mark.parametrize("row", EXPECTED, ids=[row[0] for row in EXPECTED])
def test_sentence_names_the_right_element_first_for_every_type(row):
    derived_type, first, family, expected = row
    sentence = plain_terms_sentence(
        source_name=SRC, target_name=TGT, relation_type=derived_type, depth=2, confidence=None
    )
    assert sentence == expected
    # Which stored end is named first ...
    named_first = "source" if sentence.index(SRC) < sentence.index(TGT) else "target"
    assert named_first == first
    # ... and which wording family the type uses.
    assert wording_family(derived_type) == family
    # Swapping the stored ends changes the sentence: the direction is observable.
    swapped = plain_terms_sentence(
        source_name=TGT, target_name=SRC, relation_type=derived_type, depth=2, confidence=None
    )
    assert swapped != sentence


@pytest.mark.parametrize(
    "derived_type", sorted(set(EXPECTED_BY_TYPE) - DEPENDENCY_TYPES - {"Association"})
)
def test_non_dependency_types_never_say_depends_on(derived_type):
    sentence = plain_terms_sentence(
        source_name=SRC, target_name=TGT, relation_type=derived_type, depth=2, confidence=0.95
    )
    assert "depend" not in sentence.lower()
    if derived_type == "Influence":
        assert "influence" in sentence
    # The stored source is the accessor / the whole / the specific one / the
    # influencer, and is what the sentence is about: it comes first.
    assert sentence.index(SRC) < sentence.index(TGT)


def test_association_wording_asserts_no_direction():
    sentence = plain_terms_sentence(
        source_name=SRC, target_name=TGT, relation_type="Association", depth=2, confidence=None
    )
    for directional in ("depend", "access", "influence", "made up", "includes", "specific kind", "part of"):
        assert directional not in sentence.lower()
    assert f"{SRC} and {TGT} are linked" in sentence
    # Swapping the ends changes only which name is spoken first.
    swapped = plain_terms_sentence(
        source_name=TGT, target_name=SRC, relation_type="Association", depth=2, confidence=None
    )
    assert swapped == sentence.replace(SRC, "@").replace(TGT, SRC).replace("@", TGT)


@pytest.mark.parametrize("relation_type", [None, "", "Uses", "serving", "SERVING", 7, "Serving "])
def test_type_without_wording_yields_no_sentence(relation_type):
    """A type the module has no wording for is ``None``, never a guess at which
    way it runs. (Exact, case-sensitive match: the engine emits Title-case.)"""
    assert wording_family(relation_type) is None
    assert _sentence(0.95, type_=relation_type) is None


# --- 1 hop versus N hops ----------------------------------------------------


@pytest.mark.parametrize(
    "depth, hops",
    [(1, "1 hop away"), (2, "2 hops away"), (3, "3 hops away"), (5, "5 hops away")],
)
def test_one_hop_is_singular(depth, hops):
    assert _sentence(None, depth=depth) == f"We worked this out because Billing depends on Ledger, {hops}."
    assert "1 hops" not in _sentence(0.9, depth=depth)


@pytest.mark.parametrize("depth", [0, -1, 2.0, "2", True])
def test_depth_below_one_yields_no_sentence(depth):
    assert _sentence(0.9, depth=depth) is None


# --- through cross_layer_impact ---------------------------------------------


def _element(db_session, org_id, name):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _derived(db_session, org_id, source, target, chain_element_ids, *, confidence, depth=2, derived_type="Serving"):
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    row = DerivedRelationship(
        organization_id=org_id,
        source_element_id=source.id,
        target_element_id=target.id,
        derived_type=derived_type,
        rule_id="R-07",
        chain=list(range(1, depth + 1)),
        chain_element_ids=list(chain_element_ids),
        depth=depth,
        confidence=confidence,
        provenance="derivation",
        engine_version="1.2.0",
        computed_at=_dt.datetime.utcnow(),
        stale=False,
        stale_since=None,
        stale_reason=None,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _impact(app, org_id, element_id, **kwargs):
    from flask import g

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        g.current_org_id = org_id
        return IntelligenceQueryService.cross_layer_impact(element_id, with_owner=False, **kwargs)


def test_derived_row_sentence_names_both_ends_from_the_identity_map(app, db_session, make_org):
    org = make_org("pt-names")
    ledger = _element(db_session, org.id, "Ledger")
    mid = _element(db_session, org.id, "Mid")
    billing = _element(db_session, org.id, "Billing")
    _derived(db_session, org.id, ledger, billing, [ledger.id, mid.id, billing.id], confidence=0.82)
    db_session.commit()

    # Started from Ledger (downstream) and from Billing (upstream): one fact,
    # one sentence -- the stored source is Ledger and the stored target Billing
    # whichever end the caller started from.
    downstream = _impact(app, org.id, ledger.id, include_derived=True, direction="downstream")
    upstream = _impact(app, org.id, billing.id, include_derived=True, direction="upstream")
    expected = (
        "We worked this out because Billing depends on Ledger, 2 hops away. Fairly confident (82%)."
    )
    for result in (downstream, upstream):
        derived = [r for r in result["rows"] if r["relation"]["kind"] == "derived"]
        assert [r["relation"]["plain_terms"] for r in derived] == [expected]
        # The names in the sentence are the ones in the same response's map.
        assert result["elements"][str(billing.id)]["name"] == "Billing"
        assert result["elements"][str(ledger.id)]["name"] == "Ledger"


def test_every_derived_type_reads_the_same_from_every_query_direction(app, db_session, make_org):
    """All eleven types, stored through the real store and read back through the
    service, from the stored source (downstream) and from each stored target
    (upstream). The sentence is the type's own and never reverses with the
    query direction -- this is the path that could swap source and target.
    """
    org = make_org("pt-all-types")
    source = _element(db_session, org.id, SRC)
    target_ids = {}
    for derived_type, _first, _family, _expected in EXPECTED:
        target = _element(db_session, org.id, f"{TGT}{derived_type}")
        target_ids[derived_type] = target.id
        _derived(db_session, org.id, source, target, [source.id, target.id], confidence=0.95, derived_type=derived_type)
    org_id, source_id = org.id, source.id
    db_session.commit()

    def expected_for(derived_type):
        sentence = EXPECTED_BY_TYPE[derived_type][3].replace(TGT, f"{TGT}{derived_type}")
        return f"{sentence} Very confident (95%)."

    downstream = _impact(app, org_id, source_id, include_derived=True, direction="downstream")
    got = {
        r["relation"]["type"]: r["relation"]["plain_terms"]
        for r in downstream["rows"]
        if r["relation"]["kind"] == "derived"
    }
    assert set(got) == set(EXPECTED_BY_TYPE)
    assert got == {t: expected_for(t) for t in EXPECTED_BY_TYPE}

    for derived_type, target_id in target_ids.items():
        upstream = _impact(app, org_id, target_id, include_derived=True, direction="upstream")
        rows = [r for r in upstream["rows"] if r["relation"]["kind"] == "derived"]
        assert [r["relation"]["type"] for r in rows] == [derived_type]
        assert rows[0]["relation"]["plain_terms"] == expected_for(derived_type)


def test_a_derived_type_without_wording_has_no_sentence_and_keeps_its_other_fields(app, db_session, make_org):
    org = make_org("pt-unknown-type")
    a = _element(db_session, org.id, "Alpha")
    b = _element(db_session, org.id, "Bravo")
    fact = _derived(db_session, org.id, a, b, [a.id, b.id], confidence=0.9, derived_type="Uses")
    org_id, a_id, b_id, fact_id = org.id, a.id, b.id, fact.id
    db_session.commit()

    result = _impact(app, org_id, a_id, include_derived=True)
    derived = [r for r in result["rows"] if r["relation"]["kind"] == "derived"]
    assert len(derived) == 1
    relation = derived[0]["relation"]
    assert relation["plain_terms"] is None
    assert relation["type"] == "Uses"
    assert relation["derived_id"] == fact_id
    assert {str(a_id), str(b_id)} <= set(result["elements"])  # both ends are still named in the map


def test_a_depth_one_derived_fact_reads_1_hop_through_the_service(app, db_session, make_org):
    org = make_org("pt-one-hop")
    ledger = _element(db_session, org.id, "Ledger")
    billing = _element(db_session, org.id, "Billing")
    _derived(db_session, org.id, ledger, billing, [ledger.id, billing.id], confidence=1.0, depth=1)
    org_id, ledger_id = org.id, ledger.id
    db_session.commit()

    result = _impact(app, org_id, ledger_id, include_derived=True)
    derived = [r for r in result["rows"] if r["relation"]["kind"] == "derived"]
    assert [r["relation"]["plain_terms"] for r in derived] == [
        "We worked this out because Billing depends on Ledger, 1 hop away. Very confident (100%)."
    ]


def test_derived_row_sentence_is_null_when_an_endpoint_is_another_tenants(app, db_session, make_org):
    org_a = make_org("pt-xt-a")
    org_b = make_org("pt-xt-b")
    mine = _element(db_session, org_a.id, "Mine")
    mid = _element(db_session, org_a.id, "Mid")
    theirs = _element(db_session, org_b.id, "TheirsOrgB")
    _derived(db_session, org_a.id, mine, theirs, [mine.id, mid.id, theirs.id], confidence=1.0)
    db_session.commit()

    result = _impact(app, org_a.id, mine.id, include_derived=True)
    derived = [r for r in result["rows"] if r["relation"]["kind"] == "derived"]
    assert len(derived) == 1
    assert derived[0]["relation"]["plain_terms"] is None
    assert "TheirsOrgB" not in str(result)


def test_derived_row_without_confidence_has_no_clause(app, db_session, make_org, monkeypatch):
    """The store pins ``confidence`` NOT NULL, so a null one cannot be
    written; the store accessor is wrapped to return one so the row-building
    path (not just the unit function) is shown to omit the clause."""
    from app.modules.intelligence.services import query_service

    org = make_org("pt-nullconf")
    ledger = _element(db_session, org.id, "Ledger")
    mid = _element(db_session, org.id, "Mid")
    billing = _element(db_session, org.id, "Billing")
    _derived(db_session, org.id, ledger, billing, [ledger.id, mid.id, billing.id], confidence=1.0)
    db_session.commit()

    real = query_service.list_derived_facts

    def _without_confidence(*args, **kwargs):
        facts = real(*args, **kwargs)
        for fact in facts:
            fact["confidence"] = None
        return facts

    monkeypatch.setattr(query_service, "list_derived_facts", _without_confidence)
    result = _impact(app, org.id, ledger.id, include_derived=True)
    derived = [r for r in result["rows"] if r["relation"]["kind"] == "derived"]
    assert [r["relation"]["plain_terms"] for r in derived] == [HEAD]
    assert derived[0]["relation"]["confidence"] is None
    assert "%" not in derived[0]["relation"]["plain_terms"]


def test_explicit_rows_carry_null_plain_terms(app, db_session, make_org):
    from app.models import ArchiMateRelationship

    org = make_org("pt-explicit")
    a = _element(db_session, org.id, "Alpha")
    b = _element(db_session, org.id, "Bravo")
    db_session.add(ArchiMateRelationship(source_id=a.id, target_id=b.id, type="Serving", organization_id=org.id))
    db_session.commit()

    result = _impact(app, org.id, a.id, include_derived=True)
    explicit = [r for r in result["rows"] if r["relation"]["kind"] == "explicit"]
    assert explicit and all(r["relation"]["plain_terms"] is None for r in explicit)


# --- the sentence has one generator -----------------------------------------

# Wording specific enough not to collide with unrelated copy (the solution
# explainability service already says "Very confident" / "Less confident" with
# no percentage, so those bare words are not the signature).
_SIGNATURES = (
    re.compile(r"we\s+worked\s+this\s+out\s+because", re.IGNORECASE),
    re.compile(r"hops\s+away", re.IGNORECASE),  # plural only: "one hop away" is ordinary prose elsewhere
    re.compile(r"(very|fairly)\s+confident\s*\(", re.IGNORECASE),
    re.compile(r"less\s+confident\s*\(", re.IGNORECASE),
    re.compile(r"worth\s+a\s+second\s+look", re.IGNORECASE),
)
_CODE_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".html", ".htm", ".jinja", ".jinja2", ".j2"}
# The tracked source directories that can hold a generator: application code,
# its templates and static JavaScript (``app``), the top-level template dirs,
# the SDK / tooling / script / deploy trees. Anything outside these -- a
# scratch or backup copy left at the repository root, a downloaded bundle, a
# worktree -- is not a place the product's code lives and is never scanned.
_SOURCE_ROOTS = ("app", "code_templates", "scripts", "sdk", "templates", "tools", "deploy")
_SKIP_DIRS = {".git", ".claude", ".kilo", "node_modules", "__pycache__", "site-packages", "venv", ".venv"}
# A file bigger than this is a vendored / minified bundle, not hand-written
# source. It is skipped unread, so no single read can exhaust memory; the real
# tree test asserts no Python file is ever skipped this way.
_MAX_SCAN_BYTES = 2_000_000
_GENERATOR = Path("app") / "modules" / "intelligence" / "services" / "plain_terms.py"


def _repo_root() -> Path:
    # app/modules/intelligence/tests/<this file> -> repository root
    return Path(__file__).resolve().parents[4]


def _is_test_file(path: Path) -> bool:
    return any(part in {"tests", "test"} for part in path.parts) or path.name.startswith("test_")


def _scan_for_sentence(root: Path, *, source_roots=_SOURCE_ROOTS, max_bytes: int = _MAX_SCAN_BYTES) -> dict:
    """Walk ``source_roots`` under ``root`` and report where the sentence's
    wording appears in source text.

    Returns ``{"scanned", "offenders", "generator_hits", "skipped_large"}``:
    ``offenders`` are files other than the generator carrying any signature
    phrase; ``skipped_large`` are files over ``max_bytes`` that were not read.
    Tests and documentation are excluded (tests assert on the wording, they do
    not produce it). Files are read one at a time, bounded by ``max_bytes``.
    """
    scanned = 0
    offenders = []
    skipped_large = []
    generator_hits = 0
    for top in source_roots:
        base = root / top
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for filename in filenames:
                path = Path(dirpath) / filename
                if path.suffix.lower() not in _CODE_SUFFIXES:
                    continue
                relative = path.relative_to(root)
                if _is_test_file(relative):
                    continue
                try:
                    if path.stat().st_size > max_bytes:
                        skipped_large.append(relative.as_posix())
                        continue
                    text = path.read_bytes().decode("utf-8", errors="ignore")
                except OSError:
                    continue
                scanned += 1
                hits = [sig.pattern for sig in _SIGNATURES if sig.search(text)]
                if relative == _GENERATOR:
                    generator_hits = len(hits)
                elif hits:
                    offenders.append((relative.as_posix(), hits))
    return {
        "scanned": scanned,
        "offenders": offenders,
        "generator_hits": generator_hits,
        "skipped_large": skipped_large,
    }


def test_the_sentence_is_assembled_in_exactly_one_place():
    """No other module, template or script under the tracked source directories
    carries the sentence's wording.

    This is a text check: it looks for the sentence's signature phrases in
    source text, so it catches the wording being copied into another module,
    template or script. A generator that words the sentence differently, builds
    the same output from concatenated fragments, or does either in client code
    has no signature phrase for it to find.
    """
    root = _repo_root()
    assert (root / _GENERATOR).is_file()

    found = _scan_for_sentence(root)

    # The scan is not vacuous: it walked the tree and found the generator's own wording.
    assert found["scanned"] > 500, found["scanned"]
    assert found["generator_hits"] == len(_SIGNATURES)
    # The size guard never hides Python source: only vendored / minified bundles are skipped.
    assert not [p for p in found["skipped_large"] if p.endswith(".py")], found["skipped_large"]
    assert found["offenders"] == [], found["offenders"]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


_SENTENCE_SOURCE = 'def s(a, b):\n    return f"We worked this out because {a} depends on {b}, 2 hops away."\n'
_GENERATOR_SOURCE = (
    _SENTENCE_SOURCE + "# Very confident (1). Fairly confident (1). less confident (1). Worth a second look\n"
)


def _fake_tree(tmp_path: Path) -> Path:
    _write(tmp_path / _GENERATOR, _GENERATOR_SOURCE)
    return tmp_path


def test_scan_ignores_scratch_copies_outside_the_source_roots(tmp_path):
    root = _fake_tree(tmp_path)
    _write(root / "plain_terms_backup.py", _SENTENCE_SOURCE)  # scratch copy at the repo root
    _write(root / "scratch" / "copy" / "plain_terms.py", _SENTENCE_SOURCE)
    _write(root / "backup" / "app" / "modules" / "plain_terms.py", _SENTENCE_SOURCE)
    found = _scan_for_sentence(root)
    assert found["offenders"] == []
    assert found["generator_hits"] == len(_SIGNATURES)


def test_scan_still_catches_a_copy_inside_a_source_root(tmp_path):
    root = _fake_tree(tmp_path)
    _write(root / "app" / "modules" / "other" / "second_generator.py", _SENTENCE_SOURCE)
    _write(root / "app" / "static" / "js" / "client_build.js", "const s = `We worked this out because ${a}`;\n")
    found = _scan_for_sentence(root)
    assert sorted(path for path, _ in found["offenders"]) == [
        "app/modules/other/second_generator.py",
        "app/static/js/client_build.js",
    ]


def test_scan_skips_oversized_files_without_reading_them(tmp_path, monkeypatch):
    root = _fake_tree(tmp_path)
    big = root / "app" / "static" / "vendor" / "bundle.min.js"
    _write(big, _SENTENCE_SOURCE + "x" * 500)
    real_read_bytes = Path.read_bytes

    def guarded_read_bytes(self):
        assert self != big, "an oversized file must not be read"
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    found = _scan_for_sentence(root, max_bytes=300)
    assert found["skipped_large"] == ["app/static/vendor/bundle.min.js"]
    assert found["offenders"] == []
