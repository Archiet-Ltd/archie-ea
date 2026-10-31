"""DEF-010, Capgemini dry-run: a transformation programme could never gain a
second workstream after intake. The wizard's `create_programme` inserts
exactly one `ProgrammeWorkstream`, and nothing else in
`app/modules/transformation_room/` could create one — `/solutions/programmes/
<id>/workstreams` had no create control at all.

Adds `TransformationProgrammeService.create_workstream`, mirroring the
validate-then-`CommandService.execute` shape of the sibling
`update_objective`/`archive` commands, and a POST handler on the existing
`/programmes/<id>/workstreams` route (previously GET-only).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import db
from app.models.organization import Organization
from app.models.transformation_programme import ProgrammeWorkstream
from app.models.user import User
from app.modules.transformation_room.domain import (
    ActorContext,
    NotAuthorised,
    NotFound,
    ProgrammeIntake,
)
from app.modules.transformation_room.programme_service import TransformationProgrammeService


@dataclass(frozen=True)
class Fixture:
    organization_id: int
    owner_id: int
    non_creator_id: int
    actor: ActorContext
    non_creator_actor: ActorContext


@pytest.fixture(scope="module", autouse=True)
def transformation_schema(app, _schema):
    from app.commands.reconcile_schema import _reconcile

    with app.app_context():
        _added, failed, _missing, _blocking = _reconcile(dry_run=False)
        assert failed == []


@pytest.fixture
def fixture(app, _schema):
    suffix = uuid.uuid4().hex[:12]
    with app.app_context():
        db.session.remove()
        organization = Organization(name=f"DEF010 Org {suffix}", slug=f"def010-{suffix}")
        db.session.add(organization)
        db.session.flush()
        owner = User(
            email=f"def010-owner-{suffix}@example.test",
            organization_id=organization.id,
            confirmed=True,
            enterprise_role="enterprise_architect",
        )
        non_creator = User(
            email=f"def010-viewer-{suffix}@example.test",
            organization_id=organization.id,
            confirmed=True,
            enterprise_role="business_architect",
        )
        db.session.add_all([owner, non_creator])
        db.session.flush()
        result = Fixture(
            organization_id=organization.id,
            owner_id=owner.id,
            non_creator_id=non_creator.id,
            actor=ActorContext(owner.id, organization.id, frozenset({"enterprise_architect"}), f"req-{suffix}"),
            non_creator_actor=ActorContext(
                non_creator.id, organization.id, frozenset({"business_architect"}), f"req-viewer-{suffix}"
            ),
        )
        db.session.commit()
        db.session.remove()
        try:
            yield result
        finally:
            db.session.remove()
            from app.services.org_deletion_service import OrganizationDeletionService

            if db.session.get(Organization, result.organization_id) is not None:
                OrganizationDeletionService.delete_organization(result.organization_id)
            db.session.remove()


def _intake(owner_id: int) -> ProgrammeIntake:
    return ProgrammeIntake(
        name="Simplify the application estate",
        objective="Reduce duplicated capability cost without service loss",
        owner_id=owner_id,
        target_date=date(2027, 6, 30),
        target_date_unavailable_reason=None,
        workstream_type="application_rationalisation",
        scope_expression={"business_units": ["Retail"]},
        outcome={
            "statement": "Reduce annual run cost",
            "owner_id": owner_id,
            "direction": "decrease",
            "measure": {
                "metric_name": "Annual run cost",
                "unit": "GBP",
                "currency": "GBP",
                "aggregation": "sum",
                "baseline_value": None,
                "unavailable_reason": "Finance baseline requested",
                "target_value": "900000.00",
            },
        },
    )


def test_create_workstream_adds_a_second_workstream_to_the_programme(fixture):
    created = TransformationProgrammeService.create_programme(
        actor=fixture.actor,
        command_key="def010-programme",
        request=_intake(fixture.owner_id),
    )
    programme_id = created.object_ids["programme_id"]

    result = TransformationProgrammeService.create_workstream(
        actor=fixture.actor,
        programme_id=programme_id,
        workstream_type="organisation_skills",
        objective="Realign the org design around the target operating model",
        scope_expression={"business_units": ["People"]},
        target_date=None,
        target_date_unavailable_reason="Pending HR sign-off",
        lead_id=fixture.owner_id,
        command_key="def010-add-workstream",
    )

    assert result.response["workstream_type"] == "organisation_skills"
    with Session(db.engine) as session:
        rows = session.scalars(
            select(ProgrammeWorkstream).where(
                ProgrammeWorkstream.programme_id == programme_id,
                ProgrammeWorkstream.organization_id == fixture.organization_id,
            )
        ).all()
        assert len(rows) == 2
        types = {row.workstream_type for row in rows}
        assert types == {"application_rationalisation", "organisation_skills"}


def test_create_workstream_rejects_unsupported_type(fixture):
    created = TransformationProgrammeService.create_programme(
        actor=fixture.actor,
        command_key="def010-programme-badtype",
        request=_intake(fixture.owner_id),
    )
    with pytest.raises(ValueError, match="workstream_type is not supported"):
        TransformationProgrammeService.create_workstream(
            actor=fixture.actor,
            programme_id=created.object_ids["programme_id"],
            workstream_type="not_a_real_type",
            objective="x",
            scope_expression={},
            target_date=None,
            target_date_unavailable_reason="n/a",
            lead_id=fixture.owner_id,
            command_key="def010-bad-type",
        )


def test_create_workstream_requires_a_target_date_or_a_reason(fixture):
    created = TransformationProgrammeService.create_programme(
        actor=fixture.actor,
        command_key="def010-programme-notarget",
        request=_intake(fixture.owner_id),
    )
    with pytest.raises(ValueError, match="target_date_unavailable_reason"):
        TransformationProgrammeService.create_workstream(
            actor=fixture.actor,
            programme_id=created.object_ids["programme_id"],
            workstream_type="data",
            objective="x",
            scope_expression={},
            target_date=None,
            target_date_unavailable_reason=None,
            lead_id=fixture.owner_id,
            command_key="def010-no-target",
        )


def test_create_workstream_refuses_a_user_without_create_authority(fixture):
    created = TransformationProgrammeService.create_programme(
        actor=fixture.actor,
        command_key="def010-programme-authz",
        request=_intake(fixture.owner_id),
    )
    with pytest.raises(NotAuthorised, match="workstream_create_not_authorised"):
        TransformationProgrammeService.create_workstream(
            actor=fixture.non_creator_actor,
            programme_id=created.object_ids["programme_id"],
            workstream_type="data",
            objective="x",
            scope_expression={},
            target_date=date(2027, 1, 1),
            target_date_unavailable_reason=None,
            lead_id=fixture.owner_id,
            command_key="def010-unauthorised",
        )


def test_create_workstream_rejects_unknown_programme(fixture):
    with pytest.raises(NotFound, match="programme_not_found"):
        TransformationProgrammeService.create_workstream(
            actor=fixture.actor,
            programme_id=999999999,
            workstream_type="data",
            objective="x",
            scope_expression={},
            target_date=date(2027, 1, 1),
            target_date_unavailable_reason=None,
            lead_id=fixture.owner_id,
            command_key="def010-missing-programme",
        )
