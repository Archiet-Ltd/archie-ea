"""Recording that an application supports a capability: one relationship, one projected row,
read back by the one reader."""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def seed(db_session, org, label="A"):
    """An application and a capability, each with its element, in *org*."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability

    tag = uuid.uuid4().hex[:6]
    app_row = ApplicationComponent(name=f"Monelytics {label} {tag}", organization_id=org.id)
    cap = BusinessCapability(name=f"Plans {label} {tag}", code=f"P-{tag}", organization_id=org.id, level=1)
    db_session.add_all([app_row, cap])
    db_session.flush()
    return app_row, cap


def relationship_between(db_session, org, app_row, cap, rel_type):
    from app.models.archimate_relationship_sync import _ensure_relationship

    rel = _ensure_relationship(
        db_session, rel_type, app_row.archimate_element_id, cap.archimate_element_id,
        expected_organization_id=org.id)
    db_session.flush()
    return rel


def test_recording_support_makes_one_relationship_and_one_row(db_session, make_org):
    from app.services import capability_support as cs

    org = make_org()
    app_row, cap = seed(db_session, org)
    result = cs.record_capability_support(
        org.id, app_row.archimate_element_id, cap.archimate_element_id,
        facts={"support_level": "full", "coverage_percentage": 80})
    assert result.created and result.kind == "realization"
    rows = cs.support_rows(org.id)
    assert [(r["application_name"], r["capability_name"], r["kind"], r["support_level"]) for r in rows] == [
        (app_row.name, cap.name, "realization", "primary")]
    again = cs.record_capability_support(org.id, app_row.archimate_element_id, cap.archimate_element_id)
    assert not again.created and again.row_id == result.row_id
    assert len(cs.support_rows(org.id)) == 1


def test_a_serving_relationship_made_elsewhere_counts_with_its_kind(db_session, make_org):
    from app.models.archimate_core import ArchiMateRelationship
    from app.services import capability_support as cs

    org = make_org()
    app_row, cap = seed(db_session, org)
    relationship_between(db_session, org, app_row, cap, "serving")
    rows = cs.support_rows(org.id)
    assert len(rows) == 1 and rows[0]["kind"] == "serving"
    assert [r["capability_name"] for r in cs.capabilities_of_application(org.id, app_row.id)] == [cap.name]
    assert [r["application_name"] for r in cs.applications_of_capability(org.id, rows[0]["capability_id"])] == [app_row.name]
    assert ArchiMateRelationship.query.filter_by(organization_id=org.id, type="serving").count() == 1


def test_a_pair_with_both_kinds_is_shown_once_as_realizes(db_session, make_org):
    from app.services import capability_support as cs

    org = make_org()
    app_row, cap = seed(db_session, org)
    relationship_between(db_session, org, app_row, cap, "serving")
    relationship_between(db_session, org, app_row, cap, "realization")
    rows = cs.support_rows(org.id)
    assert len(rows) == 1 and rows[0]["kind"] == "realization"


@pytest.mark.parametrize("other", ["association", "flow", "triggering"])
def test_other_relationship_types_do_not_count(db_session, make_org, other):
    from app.services import capability_support as cs

    org = make_org()
    app_row, cap = seed(db_session, org)
    relationship_between(db_session, org, app_row, cap, other)
    assert cs.support_rows(org.id) == []


def test_the_reverse_direction_does_not_count(db_session, make_org):
    from app.services import capability_support as cs

    org = make_org()
    app_row, cap = seed(db_session, org)
    from app.models.archimate_relationship_sync import _ensure_relationship

    _ensure_relationship(db_session, "realization", cap.archimate_element_id,
                         app_row.archimate_element_id, expected_organization_id=org.id)
    db_session.flush()
    assert cs.support_rows(org.id) == []


def test_deleting_superseding_or_retyping_the_relationship_removes_the_support(db_session, make_org):
    from datetime import datetime

    from app.services import capability_support as cs

    org = make_org()
    app_row, cap = seed(db_session, org)
    rel = relationship_between(db_session, org, app_row, cap, "realization")
    assert len(cs.support_rows(org.id)) == 1
    rel.type = "association"
    db_session.flush()
    assert cs.support_rows(org.id) == []
    rel.type = "serving"
    db_session.flush()
    assert len(cs.support_rows(org.id)) == 1
    rel.superseded_at = datetime.utcnow()
    db_session.flush()
    assert cs.support_rows(org.id) == []
    rel.superseded_at = None
    db_session.flush()
    assert len(cs.support_rows(org.id)) == 1
    db_session.delete(rel)
    db_session.flush()
    db_session.expire_all()
    assert cs.support_rows(org.id) == []


def test_a_refusal_names_the_problem_in_plain_words(db_session, make_org):
    from app.services import capability_support as cs

    org = make_org()
    app_row, cap = seed(db_session, org)
    with pytest.raises(cs.CapabilitySupportRefused) as bad_kind:
        cs.record_capability_support(org.id, app_row.archimate_element_id, cap.archimate_element_id, kind="flow")
    assert "Realizes or Serves" in str(bad_kind.value)
    with pytest.raises(cs.CapabilitySupportRefused) as swapped:
        cs.record_capability_support(org.id, cap.archimate_element_id, app_row.archimate_element_id)
    assert "is not an application" in str(swapped.value)
    with pytest.raises(cs.CapabilitySupportRefused) as level:
        cs.record_capability_support(org.id, app_row.archimate_element_id, cap.archimate_element_id,
                                     facts={"support_level": "huge"})
    assert "primary" in str(level.value)
    assert cs.support_rows(org.id) == []


def test_facts_can_be_changed_and_support_removed(db_session, make_org):
    from app.services import capability_support as cs

    org = make_org()
    app_row, cap = seed(db_session, org)
    result = cs.record_capability_support(org.id, app_row.archimate_element_id, cap.archimate_element_id)
    cs.update_support_facts(org.id, result.row_id, support_level="partial", notes="Billing only")
    row = cs.support_rows(org.id)[0]
    assert row["support_level"] == "partial" and row["notes"] == "Billing only"
    cs.remove_capability_support(org.id, result.row_id)
    assert cs.support_rows(org.id) == []


def test_unsupported_capabilities_and_summary(db_session, make_org):
    from app.services import capability_support as cs

    org = make_org()
    app_row, cap = seed(db_session, org)
    _, other = seed(db_session, org, "B")
    cs.record_capability_support(org.id, app_row.archimate_element_id, cap.archimate_element_id)
    assert [c.name for c in cs.unsupported_capabilities(org.id)] == [other.name]
    summary = cs.support_summary(org.id)
    assert summary["supports"] == 1 and summary["capabilities"] == 2 and summary["applications"] == 2


def test_a_capability_element_with_no_record_gets_one(db_session, make_org):
    from app.models.archimate_core import ArchiMateElement
    from app.models.unified_capability import UnifiedCapability
    from app.services import capability_support as cs

    org = make_org()
    app_row, _ = seed(db_session, org)
    element = ArchiMateElement(name="Zebra " + uuid.uuid4().hex[:5], type="Capability", layer="Strategy",
                               organization_id=org.id)
    db_session.add(element)
    db_session.flush()
    cs.record_capability_support(org.id, app_row.archimate_element_id, element.id)
    record = UnifiedCapability.query.filter_by(organization_id=org.id, archimate_element_id=element.id).one()
    assert record.name == element.name and cs.support_rows(org.id)[0]["capability_id"] == record.id


def test_a_failing_apply_reads_in_plain_words(db_session, make_org, monkeypatch):
    """The sentence a person is shown never carries database text."""
    from app.services import capability_support as cs

    org = make_org()
    app_row, cap = seed(db_session, org)

    def boom(*a, **k):
        raise RuntimeError('null value in column "organization_id" violates not-null constraint')

    monkeypatch.setattr("app.models.archimate_relationship_sync._ensure_relationship", boom)
    with pytest.raises(cs.CapabilitySupportRefused) as raised:
        cs.record_capability_support(org.id, app_row.archimate_element_id, cap.archimate_element_id)
    text = str(raised.value)
    assert text == "The link could not be saved, so nothing was changed."
    for word in ("relation", "column", "violates", "constraint", "SQL"):
        assert word not in text
