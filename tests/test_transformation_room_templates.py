"""Transformation Room information hierarchy and honest-state contracts."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.exc import SQLAlchemyError
from app.models.user import ROLE_CTO, ROLE_ENTERPRISE_ARCHITECT
from app.modules.transformation_room.domain import TypedEvidenceValue
from app.modules.transformation_room.evidence_service import sha256_canonical
from app.modules.transformation_room.gate_service import TransformationGateService
from app.modules.transformation_room.programme_service import TransformationProgrammeService
from app.modules.transformation_room.read_models import TransformationRoomReadModel
from app.utils.role_access import SIDEBAR_ZONES

from tests.test_transformation_programme_service import (
    _intake,
    programme_fixture,
    transformation_schema,
)


def _room_page(app, scope, login_as, stage="objective", *, workstream_type="process"):
    created = TransformationProgrammeService.create_programme(
        actor=scope.actor,
        command_key=f"room-template-{stage}-{workstream_type}",
        request=_intake(scope.owner_id, workstream_type=workstream_type),
    )
    client = app.test_client()
    login_as(client, scope.owner_id)
    programme_id = created.object_ids["programme_id"]
    workstream_id = created.object_ids["workstream_id"]
    response = client.get(
        f"/solutions/programmes/{programme_id}/workstreams/{workstream_id}/{stage}"
    )
    return response, response.get_data(as_text=True), programme_id, workstream_id


def test_room_has_one_breadcrumb_and_complete_programme_header(
    app, programme_fixture, login_as
):
    """Catches duplicate hierarchy and omission of persisted programme context."""
    response, body, _programme_id, _workstream_id = _room_page(
        app, programme_fixture, login_as
    )

    assert response.status_code == 200
    assert body.count('aria-label="Breadcrumb"') == 1
    for label in (
        "Objective",
        "Lifecycle",
        "Owner",
        "Next action",
        "Evidence posture",
        "Expected outcome",
    ):
        assert label in body
    assert "Finance baseline requested" in body
    assert "—" in body


def test_stage_rail_is_directly_addressable_without_claiming_future_readiness(
    app, programme_fixture, login_as
):
    """Catches later-stage links being hidden or presented as active functionality."""
    for stage in ("decision", "execute", "outcomes"):
        response, body, programme_id, workstream_id = _room_page(
            app, programme_fixture, login_as, stage=stage
        )
        assert response.status_code == 200
        assert f"/solutions/programmes/{programme_id}/workstreams/{workstream_id}/{stage}" in body
        assert "Not available in this release" in body
        assert "Ready to advance" not in body
        assert 'data-resource-state="unknown"' in body


def test_non_technology_room_contains_no_solution_or_platform_claims(
    app, programme_fixture, login_as
):
    """Catches business transformation being framed as target-platform solution design."""
    _response, body, _programme_id, _workstream_id = _room_page(
        app, programme_fixture, login_as, workstream_type="process"
    )
    lowered = body.lower()
    assert "target platform" not in lowered
    assert "clean core" not in lowered
    assert "create technology solution" not in lowered


def test_owner_picker_is_an_accessible_keyboard_combobox(
    app, programme_fixture, login_as
):
    """Catches a mouse-only owner picker or stale hidden owner identity."""
    client = app.test_client()
    login_as(client, programme_fixture.owner_id)
    body = client.get("/solutions/new-programme").get_data(as_text=True)

    assert 'role="combobox"' in body
    assert 'aria-autocomplete="list"' in body
    assert '@keydown.arrow-down.prevent="moveOwner(1)"' in body
    assert '@keydown.arrow-up.prevent="moveOwner(-1)"' in body
    assert '@keydown.enter.prevent="chooseActiveOwner()"' in body
    assert '@keydown.escape="closeOwnerPicker()"' in body
    assert ':aria-activedescendant="activeOwnerId"' in body
    assert "this.form.owner_id = null" in body


def test_architect_synthesis_names_transformation_posture_without_relabelling_solutions(
    app, programme_fixture, login_as
):
    """Catches a Solution-only answer or a solution metric relabelled as enterprise truth."""
    TransformationProgrammeService.create_programme(
        actor=programme_fixture.actor,
        command_key="room-chief-rollup",
        request=_intake(programme_fixture.owner_id, workstream_type="process"),
    )
    client = app.test_client()
    login_as(client, programme_fixture.owner_id)
    response = client.get("/solutions/architect-synthesis/api")
    body = response.get_json()

    assert response.status_code == 200
    transformation = body["transformation"]
    assert transformation["state"] == "available"
    assert transformation["non_solution_programmes"] == 1
    assert transformation["programme_count"] == 1
    # A measure that is not computed carries no value, only the reason.
    for key in ("decision_ageing", "delivery_confidence", "outcome_variance"):
        assert transformation[key]["value"] is None
    assert transformation["cross_domain_dependencies"] == {
        "reason": (
            "Dependencies are not yet classified by transformation domain; "
            "a cross-domain count is unavailable."
        ),
        "value": None,
    }


def test_room_renders_canonical_next_action_link_and_central_cards(
    app, programme_fixture, login_as
):
    response, body, programme_id, workstream_id = _room_page(
        app, programme_fixture, login_as
    )
    programme = TransformationProgrammeService.get_programme(
        actor=programme_fixture.actor, programme_id=programme_id
    )

    assert response.status_code == 200
    assert programme.next_action.action_url
    assert f'href="{programme.next_action.action_url}"' in body
    assert 'data-slot="card"' in body
    assert 'aria-current="step"' in body
    assert f"/solutions/programmes/{programme_id}/workstreams/{workstream_id}/objective" in body


def test_room_consumes_authoritative_gate_transition_map():
    assert TransformationRoomReadModel.next_stage("objective") == (
        TransformationGateService.NEXT_STAGE["objective"]
    )
    assert "_NEXT_STAGE" not in TransformationRoomReadModel.__dict__


def test_integrity_projection_suppresses_compromised_records():
    evidence = SimpleNamespace(
        id=1,
        claim_key="cost",
        classification="observed",
        freshness_status="fresh",
        observed_at=None,
        source_system="inventory",
        value_type="decimal",
        value_json="42",
        unit="GBP",
        currency="GBP",
        source_checksum="0" * 64,
    )
    valid_value = TypedEvidenceValue("decimal", "42", "GBP", "GBP")
    assert sha256_canonical(valid_value) != evidence.source_checksum

    rows, state = TransformationRoomReadModel.project_evidence((evidence,))
    versions, version_state = TransformationRoomReadModel.project_verified_versions(
        (SimpleNamespace(id=2),),
        fields=("id",),
        verifier=lambda _row: False,
        resource_label="option versions",
    )

    assert rows == ()
    assert state == {
        "state": "unavailable",
        "reason": "Evidence integrity verification failed; compromised records are hidden.",
    }
    assert versions == ()
    assert version_state["state"] == "unavailable"


def test_stage_failure_state_is_explicit(app, programme_fixture, monkeypatch):
    created = TransformationProgrammeService.create_programme(
        actor=programme_fixture.actor,
        command_key="room-stage-failure-state",
        request=_intake(programme_fixture.owner_id),
    )
    monkeypatch.setattr(
        TransformationRoomReadModel,
        "load_stage_resources",
        classmethod(
            lambda cls, **kwargs: (_ for _ in ()).throw(SQLAlchemyError("db down"))
        ),
    )

    failed = TransformationRoomReadModel.stage(
        actor=programme_fixture.actor,
        programme_id=created.object_ids["programme_id"],
        workstream_id=created.object_ids["workstream_id"],
        stage="evidence",
    )

    assert failed.resource_states["stage"]["state"] == "failed"
    assert "could not be loaded" in failed.resource_states["stage"]["reason"]


def test_every_stage_loader_failure_renders_structurally_safe_error_page(
    app, programme_fixture, login_as, monkeypatch
):
    created = TransformationProgrammeService.create_programme(
        actor=programme_fixture.actor,
        command_key="room-every-stage-failure-render",
        request=_intake(programme_fixture.owner_id),
    )
    monkeypatch.setattr(
        TransformationRoomReadModel,
        "load_stage_resources",
        classmethod(
            lambda cls, **kwargs: (_ for _ in ()).throw(SQLAlchemyError("db down"))
        ),
    )
    client = app.test_client()
    login_as(client, programme_fixture.owner_id)

    for stage in (
        "objective",
        "discover",
        "evidence",
        "options",
        "decision",
        "execute",
        "outcomes",
    ):
        response = client.get(
            "/solutions/programmes/{}/workstreams/{}/{}".format(
                created.object_ids["programme_id"],
                created.object_ids["workstream_id"],
                stage,
            )
        )
        body = response.get_data(as_text=True)
        assert response.status_code == 200, stage
        assert "Stage resources could not be loaded. Try again later." in body
        assert 'data-resource-state="failed"' in body


def test_loading_projection_is_complete_for_every_stage_template():
    reason = "Stage resources are loading."
    for stage, keys in TransformationRoomReadModel.STAGE_RESOURCE_KEYS.items():
        resources, states, reasons = (
            TransformationRoomReadModel.stage_resource_projection(
                stage, state="loading", reason=reason
            )
        )
        assert resources == {key: () for key in keys}
        assert set(states) == {"stage", *keys}
        assert all(value == {"state": "loading", "reason": reason} for value in states.values())
        assert reasons == {key: reason for key in ("stage", *keys)}


def test_objective_form_exposes_real_accessible_saving_state(
    app, programme_fixture, login_as
):
    _response, body, _programme_id, _workstream_id = _room_page(
        app, programme_fixture, login_as
    )

    assert 'x-data="{ submitting: false }"' in body
    assert '@submit="submitting = true"' in body
    assert '@pageshow.window="submitting = false"' in body
    assert ':aria-busy="submitting"' in body
    assert ':disabled="submitting"' in body
    assert "Saving…" in body


def test_technology_intake_context_is_conditional_and_non_persisting(
    app, programme_fixture, login_as
):
    client = app.test_client()
    login_as(client, programme_fixture.owner_id)
    body = client.get("/solutions/new-programme").get_data(as_text=True)

    assert "form.workstream_type === 'technology'" in body
    assert "A technology Solution may be added after programme approval" in body
    assert 'name="target_platform"' not in body
    assert 'name="vendor_key"' not in body


def test_programme_wizard_renders_every_step_and_its_navigation(
    app, programme_fixture, login_as
):
    """The whole point of a wizard is that the page renders at all.

    The first attempt at this feature shipped a url_for() to a blueprint that
    did not exist and a Jinja filter that was never written, so every request
    500'd. A 200 plus the presence of all six steps is the cheapest guard
    against that returning.
    """
    client = app.test_client()
    login_as(client, programme_fixture.owner_id)
    response = client.get("/solutions/new-programme")

    assert response.status_code == 200
    body = response.get_data(as_text=True)

    for step in (
        "step-intent",
        "step-ownership",
        "step-workstream",
        "step-outcome",
        "step-measure",
        "step-review",
    ):
        assert f'data-testid="{step}"' in body, step

    # Navigation controls, and a step nav that cannot skip ahead.
    assert 'data-testid="wizard-next"' in body
    assert 'data-testid="wizard-back"' in body
    assert 'data-testid="wizard-submit"' in body
    assert "canVisit(index)" in body

    # Each step is gated on its own index, and every one carries x-cloak, or the
    # six steps all flash on screen together before Alpine boots.
    for index in range(6):
        marker = f'x-show="currentStep === {index}"'
        assert marker in body, marker
    assert body.count("x-cloak") >= 6


def test_programme_wizard_gates_steps_and_shows_absent_values_as_em_dash(
    app, programme_fixture, login_as
):
    """Optional-but-explained fields must not be silently defaulted.

    A missing baseline is recorded as its stated reason, never as 0 -- a 0
    that means "not measured" is indistinguishable from a measured zero.
    """
    client = app.test_client()
    login_as(client, programme_fixture.owner_id)
    body = client.get("/solutions/new-programme").get_data(as_text=True)

    # Continue is disabled until the current step validates.
    assert ':disabled="!stepValid(currentStep)"' in body
    # Submit is blocked while any earlier step is incomplete.
    assert ':disabled="submitting || incompleteSteps.length > 0"' in body
    # Enter on a non-review step advances instead of creating the programme.
    assert "if (this.currentStep !== this.steps.length - 1) { this.next(); return; }" in body

    # Absent values render as an em dash, not 0 and not a blank.
    assert "return text === '' ? '—' : text;" in body
    # The earlier attempt at this feature substituted a literal scope when the
    # capability lookup returned nothing. Nothing may reappear like it.
    assert "'Enterprise'" not in body
    assert "Not specified" not in body


def test_programme_wizard_draft_survives_reload_without_breaking_on_blocked_storage(
    app, programme_fixture, login_as
):
    client = app.test_client()
    login_as(client, programme_fixture.owner_id)
    body = client.get("/solutions/new-programme").get_data(as_text=True)

    assert "transformation-programme-intake-draft" in body
    assert "restoreDraft()" in body
    # Private-mode sessionStorage throws on access; the wizard must still run.
    assert body.count("try {") >= 4
    assert "clearDraft()" in body


def test_transformation_programmes_are_discoverable_for_architect_leaders():
    for role in (ROLE_ENTERPRISE_ARCHITECT, ROLE_CTO):
        links = [link for zone in SIDEBAR_ZONES[role] for link in zone["links"]]
        assert any(
            link["endpoint"] == "solution_design.programmes_list" for link in links
        ), role
