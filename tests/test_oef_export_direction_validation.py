"""O-01: the exported OEF must never emit a relationship the ArchiMate 3.2
matrix forbids, including in the direction it is stored.

The 17 Aug 2026 QA register measured 18 of 67 exported relationships as
invalid or inverted: a realization stored backwards (the matrix permits it
one way round only) x7, and Goal -> Goal (not a permitted pairing for any
non-fallback relationship type) x3.

``ArchiMateOEFService.export_model_validated`` (app/services/archimate_oef_service.py)
now checks every relationship against ``ArchimateValidityService.is_valid``
(the same corrected matrix C-01 fixed in b1f6a8f) on the way out:
  - valid as stored -> emitted unchanged.
  - invalid as stored but valid reversed -> emitted with source/target
    swapped, and the correction is reported in ``validation_errors``.
  - invalid in both directions -> dropped from the export entirely, and the
    drop is reported in ``validation_errors``. Nothing the matrix forbids is
    ever emitted silently.

This test seeds exactly those two known-invalid shapes plus one valid
relationship as a control, exports, and asserts the exported XML contains
nothing the matrix forbids — the highest-value O-01 test per the register.
"""

from __future__ import annotations

import uuid

import xml.etree.ElementTree as ET

import pytest

NS = "{http://www.opengroup.org/xsd/archimate/3.0/}"


@pytest.fixture
def seeded_model(app, db_session):
    from app.models.archimate_core import ArchitectureModel
    from app.models.models import ArchiMateElement, ArchiMateRelationship

    with app.test_request_context("/"):
        from flask import g

        from app import db

        org = db_session.execute(
            db.text("SELECT id FROM organizations ORDER BY id LIMIT 1")
        ).scalar()
        if org is None:
            from app.models.organization import Organization

            suffix = uuid.uuid4().hex[:8]
            seeded = Organization(name=f"OEF Dir Org {suffix}", slug=f"oef-dir-{suffix}")
            db_session.add(seeded)
            db_session.flush()
            org = seeded.id
        g.current_org_id = org

        suffix = uuid.uuid4().hex[:8]
        model = ArchitectureModel(name=f"OEF Direction Test {suffix}")
        db_session.add(model)
        db_session.flush()

        goal = ArchiMateElement(name=f"Goal {suffix}", type="Goal", architecture_id=model.id)
        goal2 = ArchiMateElement(name=f"Goal2 {suffix}", type="Goal", architecture_id=model.id)
        comp = ArchiMateElement(
            name=f"Component {suffix}", type="ApplicationComponent", architecture_id=model.id
        )
        app_svc = ArchiMateElement(
            name=f"Service {suffix}", type="ApplicationService", architecture_id=model.id
        )
        node = ArchiMateElement(name=f"Node {suffix}", type="Node", architecture_id=model.id)
        tech_svc = ArchiMateElement(
            name=f"TechService {suffix}", type="TechnologyService", architecture_id=model.id
        )
        db_session.add_all([goal, goal2, comp, app_svc, node, tech_svc])
        db_session.flush()

        # Invalid #1: ApplicationService -> ApplicationComponent realization,
        # stored BACKWARDS. The matrix permits realization the other way
        # round only (a component realises the service it provides).
        # Reversible: must be emitted as ApplicationComponent -> ApplicationService.
        backwards_realization = ArchiMateRelationship(
            type="realization",
            architecture_id=model.id,
            source_id=app_svc.id,
            target_id=comp.id,
        )
        # Invalid #2: Goal -> Goal triggering — a behavioural relationship type
        # not permitted between motivation elements in either direction
        # (composition/aggregation ARE valid for Goal decomposition; triggering
        # is not). Must be dropped.
        goal_goal = ArchiMateRelationship(
            type="triggering",
            architecture_id=model.id,
            source_id=goal.id,
            target_id=goal2.id,
        )
        # Control: a genuinely valid relationship, must survive unchanged.
        valid_realization = ArchiMateRelationship(
            type="realization",
            architecture_id=model.id,
            source_id=node.id,
            target_id=tech_svc.id,
        )
        db_session.add_all([backwards_realization, goal_goal, valid_realization])
        db_session.commit()

        return {
            "model_id": model.id,
            "goal_id": goal.id,
            "goal2_id": goal2.id,
            "comp_id": comp.id,
            "app_svc_id": app_svc.id,
            "node_id": node.id,
            "tech_svc_id": tech_svc.id,
            "backwards_realization_id": backwards_realization.id,
            "goal_goal_id": goal_goal.id,
            "valid_realization_id": valid_realization.id,
        }


def test_export_corrects_or_drops_every_invalid_relationship(app, seeded_model):
    from app.services.archimate_oef_service import ArchiMateOEFService
    from app.services.archimate_validity_service import ArchimateValidityService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = None  # export_model doesn't filter by org
        service = ArchiMateOEFService()
        xml_str, errors = service.export_model_validated(model_id=seeded_model["model_id"])

    # Both known-invalid relationships must have been reported.
    assert len(errors) == 2, f"expected exactly 2 corrections/drops, got {len(errors)}: {errors}"

    root = ET.fromstring(xml_str)
    elem_type_by_id = {}
    for el in root.find(f"{NS}elements"):
        eid = el.get("identifier").replace("id-", "", 1)
        elem_type_by_id[int(eid)] = el.get(f"{{{service.XSI_NS}}}type")

    validity = ArchimateValidityService()
    rels_el = root.find(f"{NS}relationships")
    seen_source_target = set()
    for rel in rels_el:
        source_id = int(rel.get("source").replace("id-", "", 1))
        target_id = int(rel.get("target").replace("id-", "", 1))
        rel_type = rel.get(f"{{{service.XSI_NS}}}type").lower()
        source_type = elem_type_by_id[source_id]
        target_type = elem_type_by_id[target_id]
        seen_source_target.add((source_id, target_id))

        assert validity.is_valid(source_type, target_type, rel_type), (
            f"export contains a matrix-forbidden relationship: "
            f"{source_type}(id-{source_id}) --{rel_type}--> {target_type}(id-{target_id})"
        )

    # The backwards ApplicationService->ApplicationComponent realization must
    # appear reversed (ApplicationComponent -> ApplicationService), not as
    # originally stored.
    assert (seeded_model["comp_id"], seeded_model["app_svc_id"]) in seen_source_target
    assert (seeded_model["app_svc_id"], seeded_model["comp_id"]) not in seen_source_target

    # The Goal->Goal triggering (invalid both ways) must be dropped entirely.
    assert (seeded_model["goal_id"], seeded_model["goal2_id"]) not in seen_source_target
    assert (seeded_model["goal2_id"], seeded_model["goal_id"]) not in seen_source_target

    # The valid control relationship must survive unchanged.
    assert (seeded_model["node_id"], seeded_model["tech_svc_id"]) in seen_source_target


def test_validation_errors_name_the_specific_relationship(app, seeded_model):
    from app.services.archimate_oef_service import ArchiMateOEFService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = None
        service = ArchiMateOEFService()
        _xml_str, errors = service.export_model_validated(model_id=seeded_model["model_id"])

    joined = "\n".join(errors)
    assert f"id-rel-{seeded_model['backwards_realization_id']}" in joined
    assert f"id-rel-{seeded_model['goal_goal_id']}" in joined
