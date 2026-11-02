"""The result footer's confidence rule (high, medium, low) and its isolation between organisations."""

from datetime import datetime, timedelta
from types import SimpleNamespace

from app import db
from app.services import result_footer_service as svc

NOW = datetime(2026, 11, 2, 12, 0)


def _fact(**kw):
    base = dict(label="A fact", href="/x", source_kind="connector", source_label="GitHub",
                as_of=NOW - timedelta(days=10))
    base.update(kw)
    return svc.fact(**base)


def test_high_when_every_input_has_a_recorded_origin_and_is_recent():
    footer = svc.build_footer([_fact(), _fact(label="Another", source_kind="import", source_label="model.xml")], NOW)
    assert footer["confidence"] == "high"
    assert footer["sources"] == ["Synced by a connector from GitHub", "Imported from model.xml"]
    assert footer["newest_input"] == NOW - timedelta(days=10)
    assert footer["computed_at"] == NOW


def test_medium_when_an_input_is_old_undated_or_entered_by_hand():
    old = svc.build_footer([_fact(as_of=NOW - timedelta(days=91))], NOW)
    undated = svc.build_footer([_fact(as_of=None)], NOW)
    by_hand = svc.build_footer([_fact(source_kind="manual", source_label=None, who="Ada Lovelace")], NOW)
    assert [f["confidence"] for f in (old, undated, by_hand)] == ["medium"] * 3
    assert "older than 90 days" in old["confidence_reason"]
    assert "Entered by the team, Ada Lovelace" in by_hand["sources"]
    assert svc.build_footer([_fact(as_of=NOW - timedelta(days=90))], NOW)["confidence"] == "high"


def test_low_names_the_missing_input():
    footer = svc.build_footer([_fact(), svc.fact("Linked elements: none recorded", missing=True)], NOW)
    assert footer["confidence"] == "low"
    assert footer["missing"] == ["Linked elements: none recorded"]
    assert "Linked elements: none recorded" in footer["confidence_reason"]
    nothing = svc.build_footer([], NOW)
    assert nothing["confidence"] == "low" and "no input facts" in nothing["confidence_reason"]


def test_another_organisations_element_never_reaches_a_footer(db_session, make_org):
    from app.models.archimate_core import ArchiMateElement

    org_a, org_b = make_org("footer-a"), make_org("footer-b")
    mine = ArchiMateElement(name="Mine", type="ApplicationComponent", layer="application", organization_id=org_a.id)
    theirs = ArchiMateElement(name="Theirs", type="ApplicationComponent", layer="application",
                              organization_id=org_b.id, source_kind="connector", source_label="Secret connector")
    db_session.add_all([mine, theirs])
    db_session.flush()

    known = svc.element_facts(org_a.id, [mine.id, theirs.id])
    assert set(known) == {mine.id}

    risk = SimpleNamespace(likelihood=2, impact=2, owner=None, updated_at=NOW, created_at=NOW)
    touches = [{"name": "Mine", "kind": "element", "id": mine.id, "element_id": mine.id},
               {"name": "Theirs", "kind": "element", "id": theirs.id, "element_id": theirs.id}]
    footer = svc.risk_footer(risk, touches, None, known, None, lambda t: "/e/%d" % t["element_id"], NOW)
    assert footer["confidence"] == "low"
    assert footer["missing"] == [svc.NOT_IN_MODEL]
    assert "Theirs" not in repr(footer) and "Secret connector" not in repr(footer)
    assert all("Secret connector" not in s for s in footer["sources"])
    assert svc.element_facts(org_b.id, [mine.id]) == {}


def test_a_shared_reference_element_is_named_and_dated_like_any_other(db_session, make_org):
    from app.models.archimate_core import ArchiMateElement

    org = make_org("footer-shared")
    shared = ArchiMateElement(name="Shared reference", type="ApplicationComponent", layer="application",
                              organization_id=None, source_kind="import", source_label="reference.xml",
                              source_recorded_at=NOW - timedelta(days=5))
    db_session.add(shared)
    db_session.flush()
    # The tenant mixin stamps the creating organisation on flush, and the column is NOT NULL today. A shared
    # reference element has no organisation, so relax the constraint inside this test's own transaction (it
    # rolls back with the session) to hold such a row.
    db_session.execute(db.text("ALTER TABLE archimate_elements ALTER COLUMN organization_id DROP NOT NULL"))
    db_session.execute(db.text("UPDATE archimate_elements SET organization_id = NULL WHERE id = :id"),
                       {"id": shared.id})
    db_session.expire_all()

    known = svc.element_facts(org.id, [shared.id])
    assert known[shared.id]["name"] == "Shared reference"
    risk = SimpleNamespace(likelihood=2, impact=2, owner="Ada Lovelace", updated_at=NOW, created_at=NOW)
    touches = [{"name": "Shared reference", "kind": "element", "id": shared.id, "element_id": shared.id}]
    footer = svc.risk_footer(risk, touches, None, known, None, lambda t: "/e/%d" % t["element_id"], NOW)
    assert footer["missing"] == []
    assert [e["label"] for e in footer["evidence"]][1:] == ["Shared reference"]
    assert footer["newest_input"] == NOW
    assert "no longer recorded" not in repr(footer)


def test_source_pairs_each_origin_with_the_fact_it_belongs_to():
    footer = svc.build_footer([
        _fact(label="Recorded score", subject="Score", source_kind="manual", source_label=None, who="Ada Lovelace"),
        _fact(label="Spoke", subject="Element", source_kind="unknown", source_label=None),
        _fact(label="Hub", subject="Element", source_kind="unknown", source_label=None),
    ], NOW)
    assert footer["sources"] == ["Score: entered by the team, Ada Lovelace", "Element: no recorded origin"]


def test_medium_reason_names_which_input_is_the_weak_one():
    footer = svc.build_footer([
        _fact(label="Recorded score", subject="Score", source_kind="manual", source_label=None),
        _fact(label="Spoke", subject="Spoke", source_kind="sample", source_label=None),
        _fact(label="Hub", subject="Hub", as_of=NOW - timedelta(days=200)),
        _fact(label="Fine", subject="Fine"),
    ], NOW)
    reason = footer["confidence_reason"]
    assert footer["confidence"] == "medium"
    assert "Score was entered by hand" in reason
    assert "Spoke is sample data" in reason
    assert "Hub is older than 90 days" in reason
    assert "Fine" not in reason
