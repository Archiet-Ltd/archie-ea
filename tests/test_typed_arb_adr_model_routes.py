"""Registered PostgreSQL HTTP contracts for ADR/model ARB convergence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import text

from app import db
from app.models.architecture_decision import ArchitectureDecision
from app.models.architecture_review_board import ARBReviewCycle, ARBReviewItem
from app.models.models import ArchiMateElement, ArchitectureModel
from app.models.organization import Organization
from app.models.transformation_decision import ARBSubjectEvidenceSnapshot
from app.models.user import Permission, Role, User


@dataclass(frozen=True)
class _RouteScope:
    organization_id: int
    foreign_organization_id: int
    actor_id: int
    role_id: int
    adr_id: int
    model_id: int
    foreign_adr_id: int


def _new_adr(org_id, *, complete=True):
    return ArchitectureDecision(
        organization_id=org_id,
        decision_id=f"AD-{uuid.uuid4().hex[:10]}",
        title="Adopt governed event integration",
        status="proposed",
        context="Services require dependable asynchronous integration.",
        decision="Use durable domain events through the enterprise broker.",
        rationale="This decouples delivery while retaining ownership.",
        consequences="Teams own schema compatibility." if complete else "",
    )


@pytest.fixture
def route_scope(app, _schema):
    suffix = uuid.uuid4().hex[:10]
    with app.app_context():
        db.session.remove()
        org = Organization(name=f"Typed route {suffix}", slug=f"typed-route-{suffix}")
        foreign = Organization(
            name=f"Typed route foreign {suffix}", slug=f"typed-route-foreign-{suffix}"
        )
        db.session.add_all((org, foreign))
        db.session.flush()
        role = Role(
            name=f"Typed route architect {suffix}",
            permissions=Permission.GENERAL,
            index="main",
            default=False,
        )
        db.session.add(role)
        db.session.flush()
        actor = User(
            organization_id=org.id,
            email=f"typed-route-{suffix}@example.test",
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        actor.role = role
        db.session.add(actor)
        db.session.flush()
        model = ArchitectureModel(
            organization_id=org.id,
            name="Persisted target architecture",
            version="1.0",
            user_id=actor.id,
        )
        adr = _new_adr(org.id)
        foreign_adr = _new_adr(foreign.id)
        db.session.add_all((model, adr, foreign_adr))
        db.session.flush()
        db.session.add(
            ArchiMateElement(
                organization_id=org.id,
                architecture_id=model.id,
                name="Payments Service",
                type="ApplicationComponent",
                layer="application",
            )
        )
        db.session.commit()
        scope = _RouteScope(
            org.id,
            foreign.id,
            actor.id,
            role.id,
            adr.id,
            model.id,
            foreign_adr.id,
        )
        db.session.remove()
        try:
            yield scope
        finally:
            db.session.remove()
            from app.services.org_deletion_service import OrganizationDeletionService

            # The foreign organisation first: it may deliberately cite the primary
            # organisation's rows, so the citing side must be gone before the cited
            # organisation is deleted. The deletion service resolves the
            # arb_submission_evidence_snapshots/arb_review_items/arb_review_cycles
            # append-only ring itself, via the one deferrable edge in that cycle.
            for org_id in (scope.foreign_organization_id, scope.organization_id):
                if db.session.get(Organization, org_id) is not None:
                    OrganizationDeletionService.delete_organization(org_id)
            db.session.remove()
            with db.engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM roles WHERE id = :role_id"),
                    {"role_id": scope.role_id},
                )


def _login(client, user_id, app=None):
    from flask import current_app, g, has_app_context

    from tests._session_test_helpers import mint_test_sid

    _app = app
    if _app is None and has_app_context():
        _app = current_app._get_current_object()
    _sid = mint_test_sid(user_id, app=_app)
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
        if _sid:
            session["_sid"] = _sid
    if has_app_context():
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)


def _assert_typed(body, *, subject_type, subject_id):
    assert body["success"] is True
    assert body["review_id"] == body["review_item_id"]
    assert body["review_number"].startswith("REV-")
    assert isinstance(body["snapshot_id"], int)
    assert isinstance(body["review_cycle_id"], int)
    cycle = db.session.execute(
        db.select(ARBReviewCycle).where(
            ARBReviewCycle.id == body["review_cycle_id"],
            ARBReviewCycle.subject_type == subject_type,
            ARBReviewCycle.subject_id == subject_id,
        )
    ).scalar_one()
    assert cycle.review_item.id == body["review_item_id"]


def test_registered_adr_route_submits_replays_and_ignores_forged_state(
    app, route_scope
):
    client = app.test_client()
    _login(client, route_scope.actor_id)
    first = client.post(
        f"/arb/api/adr/{route_scope.adr_id}/submit_review",
        json={
            "human_reviewed": True,
            "actor_id": route_scope.actor_id + 999,
            "readiness": {"ready": True},
            "evidence": {"passed": True},
        },
        headers={"Idempotency-Key": f"adr-route-{uuid.uuid4().hex}"},
    )
    replay = client.post(
        f"/arb/api/adr/{route_scope.adr_id}/submit_review",
        json={"human_reviewed": True},
    )

    assert first.status_code == replay.status_code == 200
    with app.app_context():
        first_body, replay_body = first.get_json(), replay.get_json()
        _assert_typed(first_body, subject_type="adr", subject_id=route_scope.adr_id)
        assert first_body["canonical_url"].endswith(f"/{route_scope.adr_id}")
        assert first_body["idempotent"] is False
        assert replay_body["review_id"] == first_body["review_id"]
        assert replay_body["review_cycle_id"] == first_body["review_cycle_id"]
        assert replay_body["snapshot_id"] == first_body["snapshot_id"]
        assert replay_body["idempotent"] is True
        assert ARBReviewItem.query.filter_by(adr_id=route_scope.adr_id).count() == 1


def test_concurrent_registered_adr_submissions_converge_on_one_cycle(app, route_scope):
    def submit(suffix):
        client = app.test_client()
        _login(client, route_scope.actor_id, app=app)
        response = client.post(
            f"/arb/api/adr/{route_scope.adr_id}/submit_review",
            json={"human_reviewed": True},
            headers={"Idempotency-Key": f"adr-concurrent-{suffix}-{uuid.uuid4().hex}"},
        )
        return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ("a", "b")))

    assert [status for status, _body in outcomes] == [200, 200]
    bodies = [body for _status, body in outcomes]
    assert len({body["review_id"] for body in bodies}) == 1
    assert len({body["review_cycle_id"] for body in bodies}) == 1
    assert len({body["snapshot_id"] for body in bodies}) == 1
    assert sorted(body["idempotent"] for body in bodies) == [False, True]
    with app.app_context():
        assert ARBReviewItem.query.filter_by(adr_id=route_scope.adr_id).count() == 1
        assert ARBReviewCycle.query.filter_by(
            subject_type="adr", subject_id=route_scope.adr_id
        ).count() == 1


def test_generic_json_and_ajax_creation_use_exactly_one_typed_subject(app, route_scope):
    client = app.test_client()
    _login(client, route_scope.actor_id)
    model_response = client.post(
        "/arb/api/reviews",
        json={
            "architecture_model_id": route_scope.model_id,
            "human_reviewed": True,
            "submitted_by_id": route_scope.actor_id + 999,
            "governance_result": {"allowed": True},
        },
    )
    adr_response = client.post(
        "/arb/reviews/create",
        json={"adr_id": route_scope.adr_id, "human_reviewed": True},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert model_response.status_code == 200
    assert adr_response.status_code == 201
    with app.app_context():
        model_body = model_response.get_json()
        _assert_typed(
            model_body,
            subject_type="architecture_model",
            subject_id=route_scope.model_id,
        )
        assert model_body["redirect_url"].endswith(
            f"/arb/reviews/{model_body['review_id']}"
        )
        assert model_body["canonical_url"] == "/architecture/models"
        adr_body = adr_response.get_json()
        assert adr_body["id"] == adr_body["review_id"] == adr_body["review_item_id"]
        assert adr_body["canonical_url"].endswith(f"/{route_scope.adr_id}")
        assert isinstance(adr_body["snapshot_id"], int)
        assert isinstance(adr_body["review_cycle_id"], int)


def test_generic_html_creation_preserves_redirect_for_typed_adr(app, route_scope):
    client = app.test_client()
    _login(client, route_scope.actor_id)
    response = client.post(
        "/arb/reviews/create",
        data={"adr_id": str(route_scope.adr_id), "human_reviewed": "on"},
    )

    assert response.status_code == 302
    with app.app_context():
        review = ARBReviewItem.query.filter_by(adr_id=route_scope.adr_id).one()
        assert response.headers["Location"].endswith(f"/arb/reviews/{review.id}")
        assert review.review_cycle_id is not None
        assert review.subject_type == "adr"


def test_generic_creation_rejects_multiple_subject_links_without_writing(app, route_scope):
    client = app.test_client()
    _login(client, route_scope.actor_id)
    response = client.post(
        "/arb/api/reviews",
        json={
            "adr_id": route_scope.adr_id,
            "architecture_model_id": route_scope.model_id,
            "human_reviewed": True,
        },
    )

    assert response.status_code == 400
    assert response.get_json()["reason_codes"] == ["exactly_one_subject_required"]
    with app.app_context():
        assert ARBReviewItem.query.filter_by(adr_id=route_scope.adr_id).count() == 0
        assert ARBReviewItem.query.filter_by(
            architecture_model_id=route_scope.model_id
        ).count() == 0


def test_not_ready_and_cross_tenant_subjects_fail_without_writes(app, route_scope):
    with app.app_context():
        incomplete = _new_adr(route_scope.organization_id, complete=False)
        db.session.add(incomplete)
        db.session.commit()
        incomplete_id = incomplete.id
        db.session.remove()
    client = app.test_client()
    _login(client, route_scope.actor_id)
    blocked = client.post(
        f"/arb/api/adr/{incomplete_id}/submit_review",
        json={"human_reviewed": True, "ready": True, "reason_codes": []},
    )
    foreign = client.post(
        f"/arb/api/adr/{route_scope.foreign_adr_id}/submit_review",
        json={"human_reviewed": True},
    )

    assert blocked.status_code == 422
    assert "adr_consequences_required" in blocked.get_json()["reason_codes"]
    assert foreign.status_code == 404
    with app.app_context():
        assert ARBReviewItem.query.filter(
            ARBReviewItem.adr_id.in_((incomplete_id, route_scope.foreign_adr_id))
        ).count() == 0


def test_stale_evidence_rolls_back_snapshot_and_review(app, route_scope, monkeypatch):
    from app.modules.transformation_room.arb_adapters import ADRARBAdapter

    original = ADRARBAdapter.snapshot

    def mutate_then_snapshot(self, actor, subject, readiness, **kwargs):
        row = db.session.execute(
            db.select(ArchitectureDecision).where(
                ArchitectureDecision.id == subject.subject_id,
                ArchitectureDecision.organization_id == actor.organization_id,
            )
        ).scalar_one()
        row.consequences = "Evidence changed after readiness evaluation."
        db.session.flush()
        return original(self, actor, subject, readiness, **kwargs)

    monkeypatch.setattr(ADRARBAdapter, "snapshot", mutate_then_snapshot)
    client = app.test_client()
    _login(client, route_scope.actor_id)
    response = client.post(
        f"/arb/api/adr/{route_scope.adr_id}/submit_review",
        json={"human_reviewed": True},
    )

    assert response.status_code == 409
    assert response.get_json()["reason_codes"] == ["arb_readiness_stale"]
    with app.app_context():
        assert ARBReviewItem.query.filter_by(adr_id=route_scope.adr_id).count() == 0
        assert ARBSubjectEvidenceSnapshot.query.filter_by(
            adr_id=route_scope.adr_id
        ).count() == 0


def test_new_typed_submission_never_adopts_a_legacy_draft(app, route_scope):
    with app.app_context():
        legacy = ARBReviewItem(
            organization_id=route_scope.organization_id,
            review_number=f"LEGACY-{uuid.uuid4().hex[:10]}",
            title="Historical draft",
            description="Created before typed convergence.",
            review_type="architecture_change",
            adr_id=route_scope.adr_id,
            submitter_id=route_scope.actor_id,
            status="draft",
        )
        db.session.add(legacy)
        db.session.commit()
        legacy_id = legacy.id
        db.session.remove()
    client = app.test_client()
    _login(client, route_scope.actor_id)
    response = client.post(
        f"/arb/api/adr/{route_scope.adr_id}/submit_review",
        json={"human_reviewed": True},
    )

    assert response.status_code == 200
    assert response.get_json()["review_id"] != legacy_id
    with app.app_context():
        historical = db.session.get(ARBReviewItem, legacy_id)
        assert historical.status == "draft"
        assert historical.review_cycle_id is None
        assert historical.subject_type is None


def test_solution_composer_requires_persisted_model_and_submits_that_model(
    app, route_scope, monkeypatch
):
    from app.modules.solutions_strategic.v2.routes import solution_composer_routes

    service = SimpleNamespace(
        current_canvas=SimpleNamespace(canvas_id=81, name="Persisted canvas")
    )
    monkeypatch.setattr(solution_composer_routes, "_get_service", lambda: service)
    client = app.test_client()
    _login(client, route_scope.actor_id)
    missing = client.post(
        "/api/solution-composer/submit-to-arb",
        json={"human_reviewed": True, "elements": [{"invented": "model"}]},
    )
    success = client.post(
        "/api/solution-composer/submit-to-arb",
        json={"architecture_model_id": route_scope.model_id, "human_reviewed": True},
    )

    assert missing.status_code == 422
    assert missing.get_json()["reason_codes"] == ["architecture_model_required"]
    assert success.status_code == 200
    data = success.get_json()["data"]
    assert data["review_id"] == data["review_item_id"]
    assert data["status"] == "submitted"
    assert data["arb_dashboard_url"] == "/arb/reviews"
    assert data["canonical_url"] == "/architecture/models"
    assert isinstance(data["snapshot_id"], int)
    assert isinstance(data["review_cycle_id"], int)


@pytest.mark.parametrize(
    "service_path",
    (
        "app.services.arb_governance_service.ARBGovernanceService",
        "app.modules.solutions_strategic.v2.services.arb_governance_service.ARBGovernanceService",
    ),
)
def test_legacy_governance_facades_reject_raw_typed_writes(service_path):
    module_name, class_name = service_path.rsplit(".", 1)
    service_type = getattr(__import__(module_name, fromlist=[class_name]), class_name)
    service = service_type()
    for subject_kwargs in ({"adr_id": 9}, {"architecture_model_id": 9}):
        with pytest.raises(ValueError, match="canonical.*submission service"):
            service.submit_for_review(
                title="Bypass",
                description="Bypass",
                review_type="architecture_change",
                submitter_id=7,
                **subject_kwargs,
            )


def test_unauthenticated_typed_write_keeps_login_floor(app, route_scope):
    response = app.test_client().post(
        f"/arb/api/adr/{route_scope.adr_id}/submit_review",
        json={"human_reviewed": True},
    )
    assert response.status_code in {302, 401}
    with app.app_context():
        assert ARBReviewItem.query.filter_by(adr_id=route_scope.adr_id).count() == 0


def test_typed_adr_write_keeps_general_permission_and_csrf_floors(
    app, route_scope
):
    from app._bootstrap.csrf_coverage import audit

    with app.app_context():
        role = db.session.get(Role, route_scope.role_id)
        role.permissions = 0
        db.session.commit()
        csrf_entry = next(
            entry
            for entry in audit(app)["protected"]
            if entry["endpoint"] == "arb.api_submit_adr_review"
        )
        assert csrf_entry["rule"] == "/arb/api/adr/<int:adr_id>/submit_review"
        db.session.remove()

    client = app.test_client()
    _login(client, route_scope.actor_id)
    response = client.post(
        f"/arb/api/adr/{route_scope.adr_id}/submit_review",
        json={"human_reviewed": True},
    )

    assert response.status_code == 403
    assert response.get_json()["code"] == "PERMISSION_DENIED"
    with app.app_context():
        assert ARBReviewItem.query.filter_by(adr_id=route_scope.adr_id).count() == 0
