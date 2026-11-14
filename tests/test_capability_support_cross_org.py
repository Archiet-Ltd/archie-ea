"""Support recorded in one organisation is invisible to, and unwritable from, another."""
from __future__ import annotations

import pytest

from tests.test_capability_support_writer import relationship_between, seed

pytestmark = pytest.mark.usefixtures("db_session")


def _two_orgs(db_session, make_org):
    org_a, org_b = make_org("support-a"), make_org("support-b")
    return (org_a, *seed(db_session, org_a, "A")), (org_b, *seed(db_session, org_b, "B"))


def test_an_organisation_reads_only_its_own_support(db_session, make_org):
    from app.modules.genome.services.coverage_slice import build_coverage_slice
    from app.services import capability_support as cs

    (org_a, app_a, cap_a), (org_b, app_b, cap_b) = _two_orgs(db_session, make_org)
    relationship_between(db_session, org_a, app_a, cap_a, "realization")
    assert [r["application_name"] for r in cs.support_rows(org_a.id)] == [app_a.name]
    assert cs.support_rows(org_b.id) == []
    assert cs.support_summary(org_b.id)["supports"] == 0
    assert cs.count_support_relationships(org_b.id) == 0
    assert build_coverage_slice(org_a.id)["cells"] and build_coverage_slice(org_b.id)["cells"] == []
    assert [c.name for c in cs.unsupported_capabilities(org_b.id)] == [cap_b.name]


def test_an_element_of_another_organisation_is_refused_and_nothing_is_written(db_session, make_org):
    from app.models.archimate_core import ArchiMateRelationship
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping as Row
    from app.services import capability_support as cs

    (org_a, app_a, cap_a), (org_b, app_b, cap_b) = _two_orgs(db_session, make_org)
    with pytest.raises(cs.CapabilitySupportRefused):
        cs.record_capability_support(org_a.id, app_a.archimate_element_id, cap_b.archimate_element_id)
    with pytest.raises(cs.CapabilitySupportRefused):
        cs.record_capability_support(org_a.id, app_b.archimate_element_id, cap_a.archimate_element_id)
    assert ArchiMateRelationship.query.filter(
        ArchiMateRelationship.type.in_(("realization", "serving")),
        ArchiMateRelationship.source_id.in_((app_a.archimate_element_id, app_b.archimate_element_id)),
    ).count() == 0
    assert Row.query.filter(Row.organization_id.in_((org_a.id, org_b.id))).count() == 0


def test_a_name_in_another_organisation_resolves_to_nothing(db_session, make_org):
    from app.services import capability_support as cs

    (org_a, app_a, cap_a), (org_b, app_b, cap_b) = _two_orgs(db_session, make_org)
    with pytest.raises(cs.CapabilitySupportRefused) as raised:
        cs.resolve_support_ends(org_a.id, app_a.name, cap_b.name)
    assert str(raised.value) == f"No capability named '{cap_b.name}' exists in this workspace."


def test_a_row_of_another_organisation_cannot_be_changed_or_removed(db_session, make_org):
    from app.services import capability_support as cs

    (org_a, app_a, cap_a), (org_b, app_b, cap_b) = _two_orgs(db_session, make_org)
    made = cs.record_capability_support(org_b.id, app_b.archimate_element_id, cap_b.archimate_element_id)
    with pytest.raises(cs.CapabilitySupportRefused):
        cs.update_support_facts(org_a.id, made.row_id, notes="not mine")
    with pytest.raises(cs.CapabilitySupportRefused):
        cs.remove_capability_support(org_a.id, made.row_id)
    assert len(cs.support_rows(org_b.id)) == 1


def test_a_relationship_across_two_organisations_is_never_projected(db_session, make_org):
    from app.models.archimate_core import ArchiMateRelationship
    from app.services import capability_support as cs

    (org_a, app_a, cap_a), (org_b, app_b, cap_b) = _two_orgs(db_session, make_org)
    db_session.add(ArchiMateRelationship(
        type="realization", source_id=app_a.archimate_element_id, target_id=cap_b.archimate_element_id,
        organization_id=org_a.id))
    db_session.flush()
    assert cs.support_rows(org_a.id) == [] and cs.support_rows(org_b.id) == []


def test_the_two_organisations_application_pages_list_only_their_own(db_session, make_org):
    from app.services import capability_support as cs
    from app.services.application_capabilities import application_capability_links

    (org_a, app_a, cap_a), (org_b, app_b, cap_b) = _two_orgs(db_session, make_org)
    cs.record_capability_support(org_a.id, app_a.archimate_element_id, cap_a.archimate_element_id)
    assert len(application_capability_links(app_a.id, org_a.id)) == 1
    assert application_capability_links(app_a.id, org_b.id) == []
    assert application_capability_links(app_b.id, org_b.id) == []
