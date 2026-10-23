"""Association is permitted between any two ArchiMate elements (3.2 section 5.4.3),
and an OEF import keeps a relationship whose type the matrix refuses for a pair,
as an Association that records the modelled type, rather than dropping it."""

import pytest

from app.config.archimate_relationship_matrix import (
    ALL_ELEMENTS,
    is_valid_relationship,
)

REFUSED_PAIRS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/"
       xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
       identifier="id-model-assoc" version="3.0">
  <name xml:lang="en">Refused pairs</name>
  <elements>
    <element identifier="e-cap" xsi:type="Capability"><name xml:lang="en">Cap</name></element>
    <element identifier="e-val" xsi:type="Value"><name xml:lang="en">Val</name></element>
    <element identifier="e-app" xsi:type="ApplicationComponent"><name xml:lang="en">App</name></element>
    <element identifier="e-coa" xsi:type="CourseOfAction"><name xml:lang="en">Coa</name></element>
    <element identifier="e-out" xsi:type="Outcome"><name xml:lang="en">Out</name></element>
    <element identifier="e-res" xsi:type="Resource"><name xml:lang="en">Res</name></element>
    <element identifier="e-fn" xsi:type="BusinessFunction"><name xml:lang="en">Fn</name></element>
    <element identifier="e-plt" xsi:type="Plateau"><name xml:lang="en">Plt</name></element>
    <element identifier="e-ac" xsi:type="ApplicationComponent"><name xml:lang="en">AppC</name></element>
  </elements>
  <relationships>
    <relationship identifier="r1" xsi:type="Realization" source="e-cap" target="e-val"/>
    <relationship identifier="r2" xsi:type="Association" source="e-val" target="e-app"/>
    <relationship identifier="r3" xsi:type="Realization" source="e-coa" target="e-out"/>
    <relationship identifier="r4" xsi:type="Assignment" source="e-res" target="e-fn"/>
    <relationship identifier="r5" xsi:type="Association" source="e-out" target="e-plt"/>
    <relationship identifier="r6" xsi:type="Realization" source="e-ac" target="e-cap"/>
  </relationships>
</model>
"""


def test_every_pair_allows_association_in_both_directions():
    missing = [
        (a, b)
        for a in ALL_ELEMENTS
        for b in ALL_ELEMENTS
        if not is_valid_relationship(a, b, "association")
    ]
    assert not missing, "association refused for %d pairs, e.g. %s" % (len(missing), missing[:5])


@pytest.mark.parametrize("pair,rel", [
    (("CourseOfAction", "Outcome"), "realization"),
    (("Resource", "BusinessFunction"), "assignment"),
    (("ApplicationComponent", "Capability"), "realization"),
    (("Capability", "Value"), "association"),
    (("Value", "Capability"), "association"),
])
def test_standard_pairs_keep_their_specific_type(pair, rel):
    assert is_valid_relationship(pair[0], pair[1], rel)


@pytest.fixture
def org_ctx(app, db_session, make_org, tenant_ctx):
    org = make_org("assoc-import")
    with tenant_ctx(org.id):
        yield org


def test_import_keeps_all_six_refused_pairs(app, org_ctx):
    from app.models.archimate_core import ArchiMateRelationship
    from app.services.archimate_import_service import ArchiMateImportService

    service = ArchiMateImportService()
    parsed = service.parse_oef_xml(REFUSED_PAIRS_XML)
    result = service.execute_import(parsed, strategy="skip_duplicates")

    assert result["relationships_failed"] == []
    assert result["relationships_created"] == 6
    assert len(result["relationship_notices"]) == 1
    assert "Realization" in result["relationship_notices"][0]

    retyped = ArchiMateRelationship.query.filter_by(type="association").all()
    docs = [r.description or "" for r in retyped]
    assert any("As modelled: Realization." in d for d in docs)
    assert ArchiMateRelationship.query.filter_by(type="assignment").count() == 1
    # ApplicationComponent -> Capability Realization is stored in the modelled
    # (forward) direction, alongside CourseOfAction -> Outcome.
    assert ArchiMateRelationship.query.filter_by(type="realization").count() == 2
    fwd = ArchiMateRelationship.query.filter_by(type="realization").all()
    from app.models.archimate_core import ArchiMateElement

    appc = ArchiMateElement.query.filter_by(name="AppC").one().id
    cap = ArchiMateElement.query.filter_by(name="Cap").one().id
    assert any(r.source_id == appc and r.target_id == cap for r in fwd)
