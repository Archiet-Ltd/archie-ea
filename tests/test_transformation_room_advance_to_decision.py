"""Advancing a workstream out of Options must land on a page that exists."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db
from app.models.transformation_programme import ProgrammeWorkstream
from app.modules.transformation_room.gate_service import TransformationGateService
from app.modules.transformation_room.read_models import STAGE_ROUTES

from tests.test_decision_brief_service import _freeze_options, decision_guard_schema  # noqa: F401
from tests.test_transformation_evidence_service import evidence_scope  # noqa: F401
from tests.test_transformation_option_service import decision_scope  # noqa: F401


def _workstream(scope):
    with Session(db.engine) as session:
        row = session.scalar(
            select(ProgrammeWorkstream).where(
                ProgrammeWorkstream.organization_id == scope.organization_id,
                ProgrammeWorkstream.id == scope.workstream_id,
            )
        )
        return row.programme_id, row.lifecycle_stage, row.revision


def test_advance_from_options_lands_on_a_page_that_renders(app, decision_scope, login_as):
    """Catches a redirect to a lifecycle stage name that is not a room page."""
    scope = decision_scope
    _freeze_options(scope)
    programme_id, stage, revision = _workstream(scope)
    assert stage == "options"
    client = app.test_client()
    login_as(client, scope.actor_id)

    base = f"/solutions/programmes/{programme_id}/workstreams/{scope.workstream_id}"
    response = client.post(
        f"{base}/options/advance",
        data={"expected_revision": str(revision), "command_key": "advance-options-to-decision"},
    )

    assert response.status_code == 303
    assert _workstream(scope)[1] == "decision_ready"
    location = response.headers["Location"]
    assert location.rsplit("/", 1)[-1] in STAGE_ROUTES
    landed = client.get(location)
    assert landed.status_code == 200


def test_every_gate_stage_maps_to_a_room_page():
    """Every lifecycle stage the gate can hand back resolves to a routable page."""
    for target in TransformationGateService.NEXT_STAGE.values():
        assert TransformationGateService.page_for_stage(target) in STAGE_ROUTES
