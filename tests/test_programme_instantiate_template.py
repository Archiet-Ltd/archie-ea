"""R1-06: instantiate a programme type template into programme records
(T-E1, T-E2, T-A2's instantiate half, count-before-and-after).

instantiate_template runs in CommandService's fenced Session(db.engine), so
these tests commit real fixture rows through their own Session and clean up
manually (see tests/test_workstream_archimate_backbone.py for the reason the
db_session fixture cannot be used here).
"""

from __future__ import annotations

import threading
import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import db
from app.modules.transformation_room.domain import ActorContext, NotAuthorised
from app.modules.transformation_room.programme_service import TransformationProgrammeService


def _outcome(owner_id):
    return {
        "statement": "Reduce close time",
        "owner_id": owner_id,
        "direction": "decrease",
        "measure": {
            "metric_name": "Days to close",
            "unit": "days",
            "aggregation": "average",
            "baseline_value": None,
            "unavailable_reason": "Not yet measured",
            "target_value": 5,
        },
    }


@pytest.fixture
def typed_journey(app):
    from sqlalchemy import select

    from app.models.architecture_journey import ArchitectureJourney
    from app.models.organization import Organization
    from app.models.user import Permission, Role, User

    suffix = uuid.uuid4().hex[:10]
    with app.app_context():
        with Session(db.engine) as session, session.begin():
            org = Organization(name=f"Inst Org {suffix}", slug=f"inst-{suffix}")
            session.add(org)
            session.flush()
            role = session.scalar(select(Role).where(Role.default.is_(True)))
            if role is None:
                role = Role(name="Architect", permissions=Permission.GENERAL, index="main", default=True)
                session.add(role)
                session.flush()
            users = {}
            for label, persona in (("ea", "enterprise_architect"), ("lead", "solution_architect"),
                                   ("admin", "platform_admin")):
                user = User(email=f"inst-{label}-{suffix}@example.test", confirmed=True,
                            organization_id=org.id, enterprise_role=persona, role=role)
                if label == "admin":
                    user.is_org_admin = True
                    user.is_platform_admin = True
                session.add(user)
                users[label] = user
            session.flush()
            journey = ArchitectureJourney(
                owner_id=users["ea"].id, organization_id=org.id, title="Typed journey",
                intent="portfolio_change", selected_layers=["motivation"], programme_type="s4hana",
                journey_state={},
            )
            session.add(journey)
            session.flush()
            ids = {"org": org.id, "journey": journey.id, **{k: v.id for k, v in users.items()}}
        yield ids
        with db.engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            org_id = ids["org"]
            conn.execute(text(
                "DELETE FROM work_package_events WHERE work_package_id IN "
                "(SELECT id FROM work_packages WHERE organization_id = :o)"), {"o": org_id})
            conn.execute(text(
                "DELETE FROM deliverables WHERE work_package_id IN "
                "(SELECT id FROM work_packages WHERE organization_id = :o)"), {"o": org_id})
            for table in ("deliverable_archimate_elements", "transformation_outbox_events", "operation_results", "command_materialisations",
                          "command_idempotency_records", "implementation_events", "work_packages",
                          "measure_definitions", "programme_outcome_commitments",
                          "programme_role_assignments", "programme_workstreams", "strategic_initiatives",
                          "architecture_journeys", "archimate_elements", "users"):
                conn.execute(text(f'DELETE FROM "{table}" WHERE organization_id = :o'), {"o": org_id})
            conn.execute(text('DELETE FROM organizations WHERE id = :o'), {"o": org_id})


def _actor(ids, who="ea"):
    persona = {"ea": "enterprise_architect", "admin": "platform_admin"}[who]
    return ActorContext(ids[who], ids["org"], frozenset({persona}), f"inst-{uuid.uuid4().hex[:6]}")


def _request(ids, leads):
    return {
        "name": "S/4HANA programme",
        "objective": "Move core finance onto the new platform.",
        "owner_id": ids["ea"],
        "target_date": None,
        "target_date_unavailable_reason": "Not yet scheduled",
        "outcome": _outcome(ids["ea"]),
        "leads": leads,
    }


def _all_leads(ids):
    from app.modules.transformation_room.programme_types.loader import ProgrammeTypeCatalogue

    return {w["key"]: ids["lead"] for w in ProgrammeTypeCatalogue().get("s4hana").workstreams}


def _counts(ids):
    org = ids["org"]
    with Session(db.engine) as s:
        q = lambda sql: s.execute(text(sql), {"o": org}).scalar()  # noqa: E731
        return {
            "programmes": q("SELECT count(*) FROM strategic_initiatives WHERE organization_id=:o"),
            "workstreams": q("SELECT count(*) FROM programme_workstreams WHERE organization_id=:o"),
            "work_packages": q("SELECT count(*) FROM work_packages WHERE organization_id=:o"),
            "deliverables": q("SELECT count(*) FROM deliverables d JOIN work_packages w "
                              "ON w.id=d.work_package_id WHERE w.organization_id=:o"),
            "events": q("SELECT count(*) FROM implementation_events WHERE organization_id=:o"),
            "elements": q("SELECT count(*) FROM archimate_elements WHERE organization_id=:o"),
        }


def test_confirm_creates_the_structure_and_a_second_confirm_changes_nothing(app, typed_journey):
    from app.modules.transformation_room.programme_types.loader import ProgrammeTypeCatalogue

    ids = typed_journey
    with app.app_context():
        template = ProgrammeTypeCatalogue().get("s4hana")
        n_deliverables = sum(len(s["deliverables"]) for s in template.stages.values())
        before = _counts(ids)
        assert before["programmes"] == 0

        result = TransformationProgrammeService.instantiate_template(
            actor=_actor(ids), journey_id=ids["journey"], command_key=uuid.uuid4().hex,
            request=_request(ids, _all_leads(ids)))
        assert result.created is True
        after = _counts(ids)
        assert after["programmes"] == 1
        assert after["workstreams"] == len(template.workstreams)
        assert after["work_packages"] == n_deliverables
        assert after["deliverables"] == n_deliverables
        assert after["events"] == len(template.stages)
        # every workstream, work package, deliverable and gate has an element
        assert after["elements"] == len(template.workstreams) + 2 * n_deliverables + len(template.stages)

        # different key, same journey (T-E2): no count changes
        TransformationProgrammeService.instantiate_template(
            actor=_actor(ids), journey_id=ids["journey"], command_key=uuid.uuid4().hex,
            request=_request(ids, _all_leads(ids)))
        assert _counts(ids) == after

        with Session(db.engine) as s:
            row = s.execute(text("SELECT programme_id, programme_template_version, arb_required_at_decide, "
                                 "outcome_type FROM architecture_journeys WHERE id=:j"),
                            {"j": ids["journey"]}).one()
        assert row.programme_id is not None
        assert row.programme_template_version == template.content_sha256
        assert row.arb_required_at_decide is False
        assert row.outcome_type == "programme"


def test_concurrent_confirms_create_one_programme(app, typed_journey):
    ids = typed_journey
    errors = []

    def go():
        try:
            with app.app_context():
                TransformationProgrammeService.instantiate_template(
                    actor=_actor(ids), journey_id=ids["journey"], command_key=uuid.uuid4().hex,
                    request=_request(ids, _all_leads(ids)))
        except Exception as exc:  # a losing racer may be refused; it must not double-create
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(timeout=100) for t in threads]
    with app.app_context():
        assert _counts(ids)["programmes"] == 1


def test_a_missing_lead_is_refused_naming_the_workstream_and_creates_nothing(app, typed_journey):
    ids = typed_journey
    with app.app_context():
        leads = _all_leads(ids)
        leads.pop("integration")
        with pytest.raises(ValueError, match="Integration"):
            TransformationProgrammeService.instantiate_template(
                actor=_actor(ids), journey_id=ids["journey"], command_key=uuid.uuid4().hex,
                request=_request(ids, leads))
        assert _counts(ids)["programmes"] == 0


def test_admin_flags_alone_cannot_instantiate(app, typed_journey):
    """T-A2's instantiate half."""
    ids = typed_journey
    with app.app_context():
        with pytest.raises(NotAuthorised):
            TransformationProgrammeService.instantiate_template(
                actor=_actor(ids, "admin"), journey_id=ids["journey"], command_key=uuid.uuid4().hex,
                request=_request(ids, _all_leads(ids)))
        assert _counts(ids)["programmes"] == 0


def test_another_orgs_journey_is_not_found(app, typed_journey):
    from app.modules.transformation_room.domain import NotFound

    ids = typed_journey
    with app.app_context():
        actor = ActorContext(ids["ea"], ids["org"] + 999999, frozenset({"enterprise_architect"}), "x")
        with pytest.raises(NotFound):
            TransformationProgrammeService.instantiate_template(
                actor=actor, journey_id=ids["journey"], command_key=uuid.uuid4().hex,
                request=_request(ids, _all_leads(ids)))


# --------------------------------------------------------------------- #
# R1-07: deliverable element credit, counts and completion               #
# --------------------------------------------------------------------- #


def _instantiate(ids):
    TransformationProgrammeService.instantiate_template(
        actor=_actor(ids), journey_id=ids["journey"], command_key=uuid.uuid4().hex,
        request=_request(ids, _all_leads(ids)))


def _deliverable_ids(ids):
    """{template_code: deliverable_id} for the instantiated programme."""
    with Session(db.engine) as s:
        rows = s.execute(text(
            "SELECT d.template_code, d.id FROM deliverables d JOIN work_packages w "
            "ON w.id = d.work_package_id WHERE w.organization_id = :o"), {"o": ids["org"]}).all()
    return {r[0]: r[1] for r in rows}


def _element(ids, element_type, name, org=None):
    from app.models.archimate_core import ArchiMateElement

    with Session(db.engine) as s, s.begin():
        el = ArchiMateElement(organization_id=org or ids["org"], name=name, type=element_type,
                              layer="Motivation", scope="enterprise", custom_properties={})
        s.add(el)
        s.flush()
        return el.id


def _status(ids, deliverable_id):
    from app.modules.transformation_room.deliverable_credit_service import credit_status

    with Session(db.engine) as s:
        return credit_status(s, ids["org"], deliverable_id)


def test_counting_reads_the_edge_table_including_pre_existing_and_shared_elements(app, typed_journey):
    from app.modules.transformation_room.deliverable_credit_service import DeliverableCreditService as S

    ids = typed_journey
    with app.app_context():
        _instantiate(ids)
        deliverables = _deliverable_ids(ids)
        frame = deliverables["s4hana.frame.business_case_and_value_drivers"]  # Driver, Goal, Assessment
        other = deliverables["s4hana.decide.decision_readiness"]  # Requirement, Assessment
        assert _status(ids, frame)["n"] == 0 and _status(ids, frame)["m"] == 3

        existing_id = _element(ids, "Assessment", "Pre-existing assessment " + uuid.uuid4().hex[:6])
        S.credit_existing(actor=_actor(ids), deliverable_id=frame, element_id=existing_id, command_key=uuid.uuid4().hex)
        S.credit_existing(actor=_actor(ids), deliverable_id=other, element_id=existing_id, command_key=uuid.uuid4().hex)
        status = _status(ids, frame)
        assert status["n"] == 1 and status["elements"][0]["credit_kind"] == "existing"
        assert _status(ids, other)["n"] == 1  # one element credited to two deliverables

        S.create_and_credit(actor=_actor(ids), deliverable_id=frame, element_type="Goal",
                            name="New goal " + uuid.uuid4().hex[:6], command_key=uuid.uuid4().hex)
        status = _status(ids, frame)
        assert status["n"] == 2
        assert {e["credit_kind"] for e in status["elements"]} == {"existing", "created"}

        # crediting the same element twice is an upsert, not a duplicate
        S.credit_existing(actor=_actor(ids), deliverable_id=frame, element_id=existing_id, command_key=uuid.uuid4().hex)
        assert len(_status(ids, frame)["elements"]) == 2

        S.remove_credit(actor=_actor(ids), deliverable_id=frame, element_id=existing_id, command_key=uuid.uuid4().hex)
        assert _status(ids, frame)["n"] == 1


def test_undeclared_type_and_foreign_org_targets_are_refused_with_no_row(app, typed_journey):
    from app.modules.transformation_room.deliverable_credit_service import DeliverableCreditService as S
    from app.modules.transformation_room.domain import NotFound

    ids = typed_journey
    with app.app_context():
        _instantiate(ids)
        frame = _deliverable_ids(ids)["s4hana.frame.business_case_and_value_drivers"]
        undeclared = _element(ids, "Node", "Undeclared node " + uuid.uuid4().hex[:6])
        with pytest.raises(ValueError, match="declares"):
            S.credit_existing(actor=_actor(ids), deliverable_id=frame, element_id=undeclared, command_key=uuid.uuid4().hex)
        with pytest.raises(ValueError, match="declares"):
            S.create_and_credit(actor=_actor(ids), deliverable_id=frame, element_type="Node",
                                name="x", command_key=uuid.uuid4().hex)

        # T-D3: a foreign org's deliverable is not found (via the accessor)
        foreign_actor = ActorContext(ids["ea"], ids["org"] + 999999, frozenset({"enterprise_architect"}), "f")
        with pytest.raises(NotFound):
            S.credit_existing(actor=foreign_actor, deliverable_id=frame, element_id=undeclared, command_key=uuid.uuid4().hex)
        assert _status(ids, frame)["elements"] == []


def test_completion_requires_a_reason_when_nothing_is_in_the_model(app, typed_journey):
    from app.modules.transformation_room.deliverable_credit_service import DeliverableCreditService as S

    ids = typed_journey
    with app.app_context():
        _instantiate(ids)
        frame = _deliverable_ids(ids)["s4hana.frame.business_case_and_value_drivers"]
        with pytest.raises(ValueError, match="why"):
            S.complete(actor=_actor(ids), deliverable_id=frame, reason=None, command_key=uuid.uuid4().hex)
        assert _status(ids, frame)["completed"] is False
        S.complete(actor=_actor(ids), deliverable_id=frame, reason="Covered in the board paper",
                   command_key=uuid.uuid4().hex)
        status = _status(ids, frame)
        assert status["completed"] is True and status["completion_reason"] == "Covered in the board paper"
        assert status["n"] == 0  # still "None yet": the reason never fakes a count


def test_credit_is_refused_without_programme_authority(app, typed_journey):
    """deliverable_complete / credit authorisation: admin flags alone, and a
    user with no persona and no assignment, cannot write."""
    from app.modules.transformation_room.deliverable_credit_service import DeliverableCreditService as S

    ids = typed_journey
    with app.app_context():
        _instantiate(ids)
        frame = _deliverable_ids(ids)["s4hana.frame.business_case_and_value_drivers"]
        with pytest.raises(NotAuthorised):
            S.complete(actor=_actor(ids, "admin"), deliverable_id=frame, reason="x", command_key=uuid.uuid4().hex)


def test_no_query_reads_source_deliverable_id_to_count_credit():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in root.rglob("*.py"):
        text_ = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text_.splitlines(), 1):
            code = line.split("#", 1)[0]
            if "source_deliverable_id" in code and "provenance" not in code and "custom_properties" in code + "":
                offenders.append(f"{path}:{lineno}")
            if "->>'source_deliverable_id'" in code or '->>"source_deliverable_id"' in code:
                offenders.append(f"{path}:{lineno}")
    assert offenders == [], offenders
