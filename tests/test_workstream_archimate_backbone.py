"""R1-05: every workstream is a WorkPackage element in the model (US-11).

Follows tests/test_tenant_isolation.py's pattern: shared db_session/make_org/
login_as fixtures, never a hand-rolled module-scoped app fixture.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def test_element_types_mapping_covers_the_three_new_source_models():
    from app.services.archimate_backbone import ELEMENT_TYPES

    assert ELEMENT_TYPES["ProgrammeWorkstream"] == ("WorkPackage", "Implementation")
    assert ELEMENT_TYPES["Deliverable"] == ("Deliverable", "Implementation")
    assert ELEMENT_TYPES["ImplementationEvent"] == ("ImplementationEvent", "Implementation")


def test_name_fields_falls_back_to_objective_last():
    from app.services.archimate_backbone import NAME_FIELDS

    assert NAME_FIELDS[-1] == "objective"
    assert NAME_FIELDS.index("name") < NAME_FIELDS.index("objective")


@pytest.fixture
def org(make_org):
    return make_org("workstream-backbone")


def test_sync_uses_explicit_organization_id_when_the_row_has_none(db_session, org):
    """Deliverable is untenanted (security.md S6); the explicit kwarg is the
    only way its sync call can know which org owns the resulting element."""
    from app.models.implementation_migration import Deliverable, WorkPackage
    from app.services.archimate_backbone import sync_archimate_element

    programme_row = _make_programme_row(db_session, org.id)
    work_package = WorkPackage(
        name=f"WP {uuid.uuid4().hex[:8]}",
        organization_id=org.id,
        strategic_initiative_id=programme_row.id,
    )
    db_session.add(work_package)
    db_session.flush()

    deliverable = Deliverable(
        name=f"Deliverable {uuid.uuid4().hex[:8]}",
        work_package_id=work_package.id,
    )
    assert getattr(deliverable, "organization_id", None) is None
    db_session.add(deliverable)
    db_session.flush()

    element = sync_archimate_element(deliverable, session=db_session, organization_id=org.id)
    assert element is not None
    assert element.organization_id == org.id
    assert deliverable.archimate_element_id == element.id


def test_sync_raises_when_no_organization_is_available_anywhere(db_session, org):
    from app.models.implementation_migration import Deliverable, WorkPackage
    from app.services.archimate_backbone import sync_archimate_element

    programme_row = _make_programme_row(db_session, org.id)
    work_package = WorkPackage(
        name=f"WP {uuid.uuid4().hex[:8]}", organization_id=org.id, strategic_initiative_id=programme_row.id
    )
    db_session.add(work_package)
    db_session.flush()

    deliverable = Deliverable(name=f"Deliverable {uuid.uuid4().hex[:8]}", work_package_id=work_package.id)
    db_session.add(deliverable)
    db_session.flush()

    with pytest.raises(ValueError, match="no organization"):
        sync_archimate_element(deliverable, session=db_session)


def test_renaming_a_workstream_never_creates_a_second_element(db_session, org):
    from app.models.transformation_programme import ProgrammeWorkstream
    from app.services.archimate_backbone import sync_archimate_element

    programme_row = _make_programme_row(db_session, org.id)
    workstream = ProgrammeWorkstream(
        organization_id=org.id,
        programme_id=programme_row.id,
        workstream_type="process",
        objective="Own the process work.",
        scope_expression={},
        lifecycle_stage="objective",
        revision=1,
    )
    db_session.add(workstream)
    db_session.flush()

    first = sync_archimate_element(workstream, session=db_session, organization_id=org.id)
    assert first is not None
    first_element_id = workstream.archimate_element_id

    workstream.name = "Renamed workstream"
    db_session.flush()
    second = sync_archimate_element(workstream, session=db_session, organization_id=org.id)
    assert second is None  # idempotent: already on the backbone
    assert workstream.archimate_element_id == first_element_id


def _make_programme_row(session, organization_id):
    from app.models.strategic import StrategicInitiative

    programme = StrategicInitiative(
        organization_id=organization_id,
        name=f"Programme {uuid.uuid4().hex[:8]}",
        description="Test programme for the ArchiMate backbone.",
        record_kind="transformation_programme",
        status="draft",
        revision=1,
    )
    session.add(programme)
    session.flush()
    return programme


# --------------------------------------------------------------------- #
# Manual paths get an element: the intake path and the wizard path       #
# --------------------------------------------------------------------- #


def test_intake_creates_a_workstream_with_an_element(app):
    """create_programme runs its own fenced Session(db.engine), which cannot
    see rows only committed inside the db_session fixture's outer
    transaction (that commit resolves to a SAVEPOINT release, not a real
    commit -- see tests/conftest.py). This test therefore commits its own
    fixture data through a real, separately-opened Session and cleans up
    manually, following tests/test_transformation_programme_service.py's
    established pattern for exercising this fenced-session service."""
    from sqlalchemy.orm import Session as _Session
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    suffix = uuid.uuid4().hex[:10]
    with app.app_context():
        with _Session(db.engine) as session, session.begin():
            from sqlalchemy import select as _select
            from app.models.user import Role

            organization = Organization(name=f"Backbone Org {suffix}", slug=f"backbone-{suffix}")
            session.add(organization)
            session.flush()
            # __init__'s default-role fallback queries via db.session, a
            # different session than this one -- pass `role=` explicitly,
            # queried through this same session, or attaching `user` below
            # raises "already attached to session" (same trap as R1-01's
            # T-A2/T-A3 tests; see tests/test_transformation_programme_service.py).
            default_role = session.scalar(_select(Role).where(Role.default.is_(True)))
            if default_role is None:
                from app.models.user import Permission

                default_role = Role(name="Architect", permissions=Permission.GENERAL, index="main", default=True)
                session.add(default_role)
                session.flush()
            user = User(
                email=f"backbone-ea-{suffix}@example.test",
                confirmed=True,
                organization_id=organization.id,
                enterprise_role="enterprise_architect",
                role=default_role,
            )
            session.add(user)
            session.flush()
            org_id, user_id = organization.id, user.id

        _run_intake_and_assert_element(org_id, user_id)

        with db.engine.begin() as connection:
            from sqlalchemy import text

            connection.execute(text("SET LOCAL session_replication_role = replica"))
            for table_name in (
                "transformation_outbox_events",
                "operation_results",
                "command_materialisations",
                "command_idempotency_records",
                "measure_definitions",
                "programme_outcome_commitments",
                "programme_role_assignments",
                "programme_workstreams",
                "strategic_initiatives",
                "users",
            ):
                connection.execute(
                    text(f'DELETE FROM "{table_name}" WHERE organization_id = :org'),
                    {"org": org_id},
                )
            connection.execute(text('DELETE FROM "users" WHERE id = :user_id'), {"user_id": user_id})
            connection.execute(text('DELETE FROM "organizations" WHERE id = :org'), {"org": org_id})


def _run_intake_and_assert_element(org_id, ea_user_id):
    from app.modules.transformation_room.domain import ActorContext, ProgrammeIntake
    from app.modules.transformation_room.programme_service import TransformationProgrammeService
    from app.models.transformation_programme import ProgrammeWorkstream
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from app import db

    org = type("Org", (), {"id": org_id})
    ea_user = type("U", (), {"id": ea_user_id})
    actor = ActorContext(ea_user.id, org.id, frozenset({"enterprise_architect"}), "backbone-intake")
    intake = ProgrammeIntake(
        name="Backbone intake programme",
        objective="Reduce duplicated capability cost.",
        owner_id=ea_user.id,
        target_date=None,
        target_date_unavailable_reason="Not yet scheduled",
        workstream_type="process",
        scope_expression={"business_units": ["Retail"]},
        outcome={
            "statement": "Reduce cost",
            "owner_id": ea_user.id,
            "direction": "decrease",
            "measure": {
                "metric_name": "Cost",
                "unit": "GBP",
                "currency": "GBP",
                "aggregation": "sum",
                "baseline_value": None,
                "unavailable_reason": "Not measured yet",
                "target_value": 1,
            },
        },
    )
    result = TransformationProgrammeService.create_programme(
        actor=actor, command_key=f"backbone-{uuid.uuid4().hex[:8]}", request=intake
    )
    workstream_id = result.object_ids["workstream_id"]
    with Session(db.engine) as session:
        workstream = session.scalar(
            select(ProgrammeWorkstream).where(ProgrammeWorkstream.id == workstream_id)
        )
        assert workstream.archimate_element_id is not None


def test_backfill_apply_touches_only_the_named_org_and_dry_run_changes_nothing(db_session, make_org):
    """T-D8 backfill half: --org scopes every write; dry-run writes nothing;
    a re-run is a no-op."""
    from app.commands.backfill_workstream_elements import _run
    from app.models.transformation_programme import ProgrammeWorkstream

    org_a, org_b = make_org("backfill-a"), make_org("backfill-b")
    rows = {}
    for label, org in (("a", org_a), ("b", org_b)):
        programme = _make_programme_row(db_session, org.id)
        ws = ProgrammeWorkstream(
            organization_id=org.id, programme_id=programme.id, workstream_type="process",
            objective="Legacy objective %s" % label, scope_expression={},
            lifecycle_stage="objective", revision=1,
        )
        db_session.add(ws)
        rows[label] = ws
    db_session.flush()
    db_session.commit()  # savepoint release: the CLI's dry-run rolls back

    dry = _run(dry_run=True, organization_id=org_a.id)
    assert dry["synced"] == 0
    assert rows["a"].archimate_element_id is None

    applied = _run(dry_run=False, organization_id=org_a.id)
    assert applied["synced"] == 1
    assert rows["a"].archimate_element_id is not None
    assert rows["b"].archimate_element_id is None, "another org's workstream must be untouched"

    again = _run(dry_run=False, organization_id=org_a.id)
    assert again["synced"] == 0
