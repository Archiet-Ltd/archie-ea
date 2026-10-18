"""Check the owning solution before reading or changing its mappings,
insights, reasoning and published specs.

Six routes in ``solution_design_routes.py`` act on a record keyed by a
solution id and a child id, where the child's own model carries no
organisation column at all: a capability mapping, an ArchiMate element
mapping, a Copilot insight, an AI reasoning state and a published API spec.
Each now confirms the solution is visible to the caller's active organisation
before it touches the child record, and refuses identically whether the
solution belongs to another organisation or does not exist at all.
"""

from __future__ import annotations

import re
import uuid

import pytest

NONEXISTENT_ID = 999_999_999


def _normalize(text: str) -> str:
    """Replace every digit run with a placeholder so two responses that only
    differ by which id was requested can be compared for equality."""
    return re.sub(r"\d+", "<ID>", text)


def _fresh(model, entity_id):
    """Reload ``model`` by id straight from the database.

    Two traps stack here. ``Query.get()`` short-circuits on an identity-map
    hit and skips SQL entirely (the one ``app/utils/route_guards.py``
    documents for routes, which applies to test assertions too). The second
    is more subtle: even a query that does emit SQL leaves an *already
    loaded, unexpired* attribute on an existing identity-mapped instance
    untouched by the freshly fetched row — the object the fixture built
    before the request, not the one the route's own (separately scoped)
    session wrote through. ``expire_all()`` forces a real reload of every
    attribute, so the row reflects what the route actually committed.
    """
    from app import db

    db.session.expire_all()
    return model.query.filter_by(id=entity_id).first()


def _make_user(db_session, org, *, email=None):
    from app.models.user import User

    user = User(
        email=email or f"solrec-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _make_solution(db_session, org, **kwargs):
    from app.models.solution_models import Solution

    kwargs.setdefault("name", f"Solution {uuid.uuid4().hex[:8]}")
    solution = Solution(organization_id=org.id, **kwargs)
    db_session.add(solution)
    db_session.flush()
    return solution


def _make_capability(db_session, org):
    from app.models.business_capabilities import BusinessCapability

    cap = BusinessCapability(name=f"Capability {uuid.uuid4().hex[:8]}", organization_id=org.id)
    db_session.add(cap)
    db_session.flush()
    return cap


def _make_capability_mapping(db_session, capability, *, solution=None, problem_id=None):
    from app.models.solution_models import SolutionCapabilityMapping

    mapping = SolutionCapabilityMapping(
        solution_id=solution.id if solution is not None else None,
        problem_id=problem_id,
        capability_id=capability.id,
    )
    db_session.add(mapping)
    db_session.flush()
    return mapping


def _make_problem_definition(db_session, org, user, solution):
    """A problem definition linked to ``solution`` through an analysis
    session, the path a capability mapping can hang off instead of a direct
    ``solution_id``."""
    from app.models.solution_architect_models import (
        SolutionAnalysisSession,
        SolutionProblemDefinition,
    )

    session_row = SolutionAnalysisSession(
        name=f"Session {uuid.uuid4().hex[:8]}",
        organization_id=org.id,
        created_by_id=user.id,
    )
    db_session.add(session_row)
    db_session.flush()
    solution.analysis_session_id = session_row.id
    db_session.flush()
    problem = SolutionProblemDefinition(
        session_id=session_row.id,
        organization_id=org.id,
        problem_description="test problem",
    )
    db_session.add(problem)
    db_session.flush()
    return problem


def _make_archimate_element(db_session, solution, *, name="org-a-sentinel-element"):
    from app.models.solution_models import SolutionArchiMateElement

    element = SolutionArchiMateElement(
        solution_id=solution.id,
        layer_type="application",
        element_id=1,
        element_table="test_elements",
        element_name=name,
    )
    db_session.add(element)
    db_session.flush()
    return element


def _make_copilot_insight(db_session, solution, *, title="org-a-sentinel"):
    from app.models.copilot_insight import CopilotInsight

    insight = CopilotInsight(
        solution_id=solution.id,
        insight_type="risk",
        title=title,
        body="org-a-sentinel body",
    )
    db_session.add(insight)
    db_session.flush()
    return insight


def _make_reasoning_state(db_session, solution, *, marker="org-a-sentinel-reasoning"):
    from app.models.solution_reasoning import SolutionAIReasoningState

    state = SolutionAIReasoningState(
        solution_id=solution.id,
        adm_phase="A",
        context_snapshot={"marker": marker},
        reasoning_trace={"steps": []},
    )
    db_session.add(state)
    db_session.flush()
    return state


def _make_spec(db_session, solution, *, title="org-a-sentinel-spec"):
    from app.models.published_api_spec import PublishedAPISpec

    spec = PublishedAPISpec(
        solution_id=solution.id,
        spec_type="openapi",
        spec_version="1.0.0",
        spec_content={"info": {"title": title}},
        status="published",
    )
    db_session.add(spec)
    db_session.flush()
    return spec


def _no_active_org(monkeypatch):
    """Simulate an authenticated caller with no active organisation at all.

    Every handler imports ``current_org_id`` locally (as the precedent fix
    does), so patching the attribute on its home module is picked up by the
    next call, including the before_request hook that sets ``g.current_org_id``.
    """
    import app.middleware.tenant_context as tenant_context

    monkeypatch.setattr(tenant_context, "current_org_id", lambda: None)


# =============================================================================
# C1: DELETE /solutions/<solution_id>/capabilities/<mapping_id>
# =============================================================================


class TestDeleteSolutionCapability:
    @pytest.fixture
    def scene(self, db_session, make_org):
        org_a = make_org("c1-a")
        org_b = make_org("c1-b")
        user_a = _make_user(db_session, org_a)
        user_b = _make_user(db_session, org_b)
        solution_a = _make_solution(db_session, org_a)
        solution_b = _make_solution(db_session, org_b)
        capability_a = _make_capability(db_session, org_a)
        mapping_a = _make_capability_mapping(db_session, capability_a, solution=solution_a)
        problem_a = _make_problem_definition(db_session, org_a, user_a, solution_a)
        problem_mapping_a = _make_capability_mapping(
            db_session, capability_a, problem_id=problem_a.id
        )
        db_session.commit()
        return {
            "org_a": org_a, "org_b": org_b,
            "user_a": user_a, "user_b": user_b,
            "solution_a": solution_a, "solution_b": solution_b,
            "mapping_a": mapping_a, "problem_mapping_a": problem_mapping_a,
        }

    def _delete(self, client, login_as, user, solution_id, mapping_id):
        login_as(client, user)
        return client.delete(f"/solutions/{solution_id}/capabilities/{mapping_id}")

    def test_foreign_solution_is_refused(self, app, db_session, scene, client, login_as):
        from app.models.solution_models import SolutionCapabilityMapping

        with app.app_context():
            resp = self._delete(
                client, login_as, scene["user_b"], scene["solution_a"].id, scene["mapping_a"].id
            )

        assert resp.status_code == 404
        assert b"org-a-sentinel" not in resp.data
        assert _fresh(SolutionCapabilityMapping, scene["mapping_a"].id) is not None

    def test_own_solution_foreign_mapping_is_refused(self, app, db_session, scene, client, login_as):
        from app.models.solution_models import SolutionCapabilityMapping

        with app.app_context():
            resp = self._delete(
                client, login_as, scene["user_b"], scene["solution_b"].id, scene["mapping_a"].id
            )

        assert resp.status_code == 404
        assert _fresh(SolutionCapabilityMapping, scene["mapping_a"].id) is not None

    def test_foreign_and_nonexistent_solution_are_indistinguishable(
        self, app, db_session, scene, client, login_as
    ):
        with app.app_context():
            foreign = self._delete(
                client, login_as, scene["user_b"], scene["solution_a"].id, scene["mapping_a"].id
            )
            missing = self._delete(
                client, login_as, scene["user_b"], NONEXISTENT_ID, scene["mapping_a"].id
            )

        assert foreign.status_code == missing.status_code == 404
        assert _normalize(foreign.get_data(as_text=True)) == _normalize(
            missing.get_data(as_text=True)
        )

    def test_owner_can_delete_direct_mapping(self, app, db_session, scene, client, login_as):
        from app.models.solution_models import SolutionCapabilityMapping

        with app.app_context():
            resp = self._delete(
                client, login_as, scene["user_a"], scene["solution_a"].id, scene["mapping_a"].id
            )

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        assert _fresh(SolutionCapabilityMapping, scene["mapping_a"].id) is None

    def test_owner_can_delete_problem_path_mapping(self, app, db_session, scene, client, login_as):
        from app.models.solution_models import SolutionCapabilityMapping

        with app.app_context():
            resp = self._delete(
                client,
                login_as,
                scene["user_a"],
                scene["solution_a"].id,
                scene["problem_mapping_a"].id,
            )

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        assert _fresh(SolutionCapabilityMapping, scene["problem_mapping_a"].id) is None

    def test_foreign_cannot_delete_problem_path_mapping_under_either_solution(
        self, app, db_session, scene, client, login_as
    ):
        from app.models.solution_models import SolutionCapabilityMapping

        with app.app_context():
            under_own = self._delete(
                client,
                login_as,
                scene["user_b"],
                scene["solution_b"].id,
                scene["problem_mapping_a"].id,
            )
            under_foreign = self._delete(
                client,
                login_as,
                scene["user_b"],
                scene["solution_a"].id,
                scene["problem_mapping_a"].id,
            )

        assert under_own.status_code == 404
        assert under_foreign.status_code == 404
        assert _fresh(SolutionCapabilityMapping, scene["problem_mapping_a"].id) is not None

    def test_no_active_organisation_is_refused(
        self, app, db_session, scene, client, login_as, monkeypatch
    ):
        from app.models.solution_models import SolutionCapabilityMapping

        _no_active_org(monkeypatch)
        with app.app_context():
            resp = self._delete(
                client, login_as, scene["user_a"], scene["solution_a"].id, scene["mapping_a"].id
            )

        assert resp.status_code == 404
        assert _fresh(SolutionCapabilityMapping, scene["mapping_a"].id) is not None


# =============================================================================
# C2: DELETE /solutions/<solution_id>/archimate-elements/<mapping_id>
# =============================================================================


class TestDeleteSolutionArchimateElement:
    @pytest.fixture
    def scene(self, db_session, make_org):
        org_a = make_org("c2-a")
        org_b = make_org("c2-b")
        user_a = _make_user(db_session, org_a)
        user_b = _make_user(db_session, org_b)
        solution_a = _make_solution(db_session, org_a)
        solution_b = _make_solution(db_session, org_b)
        element_a = _make_archimate_element(db_session, solution_a)
        db_session.commit()
        return {
            "org_a": org_a, "org_b": org_b,
            "user_a": user_a, "user_b": user_b,
            "solution_a": solution_a, "solution_b": solution_b,
            "element_a": element_a,
        }

    def _delete(self, client, login_as, user, solution_id, mapping_id):
        login_as(client, user)
        return client.delete(f"/solutions/{solution_id}/archimate-elements/{mapping_id}")

    def test_foreign_solution_is_refused(self, app, db_session, scene, client, login_as):
        from app.models.solution_models import SolutionArchiMateElement

        with app.app_context():
            resp = self._delete(
                client, login_as, scene["user_b"], scene["solution_a"].id, scene["element_a"].id
            )

        assert resp.status_code == 404
        assert b"org-a-sentinel" not in resp.data
        assert _fresh(SolutionArchiMateElement, scene["element_a"].id) is not None

    def test_own_solution_foreign_element_is_refused(self, app, db_session, scene, client, login_as):
        from app.models.solution_models import SolutionArchiMateElement

        with app.app_context():
            resp = self._delete(
                client, login_as, scene["user_b"], scene["solution_b"].id, scene["element_a"].id
            )

        assert resp.status_code == 404
        assert _fresh(SolutionArchiMateElement, scene["element_a"].id) is not None

    def test_foreign_and_nonexistent_solution_are_indistinguishable(
        self, app, db_session, scene, client, login_as
    ):
        with app.app_context():
            foreign = self._delete(
                client, login_as, scene["user_b"], scene["solution_a"].id, scene["element_a"].id
            )
            missing = self._delete(
                client, login_as, scene["user_b"], NONEXISTENT_ID, scene["element_a"].id
            )

        assert foreign.status_code == missing.status_code == 404
        assert _normalize(foreign.get_data(as_text=True)) == _normalize(
            missing.get_data(as_text=True)
        )

    def test_owner_can_delete(self, app, db_session, scene, client, login_as):
        from app.models.solution_models import SolutionArchiMateElement

        with app.app_context():
            resp = self._delete(
                client, login_as, scene["user_a"], scene["solution_a"].id, scene["element_a"].id
            )

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        assert _fresh(SolutionArchiMateElement, scene["element_a"].id) is None

    def test_no_active_organisation_is_refused(
        self, app, db_session, scene, client, login_as, monkeypatch
    ):
        from app.models.solution_models import SolutionArchiMateElement

        _no_active_org(monkeypatch)
        with app.app_context():
            resp = self._delete(
                client, login_as, scene["user_a"], scene["solution_a"].id, scene["element_a"].id
            )

        assert resp.status_code == 404
        assert _fresh(SolutionArchiMateElement, scene["element_a"].id) is not None


# =============================================================================
# C3: POST /solutions/<solution_id>/copilot-insights/<insight_id>/dismiss
# =============================================================================


class TestDismissCopilotInsight:
    @pytest.fixture
    def scene(self, db_session, make_org):
        org_a = make_org("c3-a")
        org_b = make_org("c3-b")
        user_a = _make_user(db_session, org_a)
        user_b = _make_user(db_session, org_b)
        solution_a = _make_solution(db_session, org_a)
        solution_b = _make_solution(db_session, org_b)
        insight_a = _make_copilot_insight(db_session, solution_a)
        db_session.commit()
        return {
            "org_a": org_a, "org_b": org_b,
            "user_a": user_a, "user_b": user_b,
            "solution_a": solution_a, "solution_b": solution_b,
            "insight_a": insight_a,
        }

    def _dismiss(self, client, login_as, user, solution_id, insight_id):
        login_as(client, user)
        return client.post(f"/solutions/{solution_id}/copilot-insights/{insight_id}/dismiss")

    def test_foreign_solution_is_refused(self, app, db_session, scene, client, login_as):
        from app.models.copilot_insight import CopilotInsight

        with app.app_context():
            resp = self._dismiss(
                client, login_as, scene["user_b"], scene["solution_a"].id, scene["insight_a"].id
            )

        assert resp.status_code == 404
        assert b"org-a-sentinel" not in resp.data
        assert _fresh(CopilotInsight, scene["insight_a"].id).dismissed is False

    def test_own_solution_foreign_insight_is_refused(self, app, db_session, scene, client, login_as):
        from app.models.copilot_insight import CopilotInsight

        with app.app_context():
            resp = self._dismiss(
                client, login_as, scene["user_b"], scene["solution_b"].id, scene["insight_a"].id
            )

        assert resp.status_code == 404
        assert _fresh(CopilotInsight, scene["insight_a"].id).dismissed is False

    def test_foreign_and_nonexistent_solution_are_indistinguishable(
        self, app, db_session, scene, client, login_as
    ):
        with app.app_context():
            foreign = self._dismiss(
                client, login_as, scene["user_b"], scene["solution_a"].id, scene["insight_a"].id
            )
            missing = self._dismiss(
                client, login_as, scene["user_b"], NONEXISTENT_ID, scene["insight_a"].id
            )

        assert foreign.status_code == missing.status_code == 404
        assert _normalize(foreign.get_data(as_text=True)) == _normalize(
            missing.get_data(as_text=True)
        )

    def test_owner_can_dismiss(self, app, db_session, scene, client, login_as):
        from app.models.copilot_insight import CopilotInsight

        with app.app_context():
            resp = self._dismiss(
                client, login_as, scene["user_a"], scene["solution_a"].id, scene["insight_a"].id
            )

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        assert _fresh(CopilotInsight, scene["insight_a"].id).dismissed is True

    def test_no_active_organisation_is_refused(
        self, app, db_session, scene, client, login_as, monkeypatch
    ):
        from app.models.copilot_insight import CopilotInsight

        _no_active_org(monkeypatch)
        with app.app_context():
            resp = self._dismiss(
                client, login_as, scene["user_a"], scene["solution_a"].id, scene["insight_a"].id
            )

        assert resp.status_code == 404
        assert _fresh(CopilotInsight, scene["insight_a"].id).dismissed is False


# =============================================================================
# C4: GET /solutions/api/<solution_id>/reasoning/<reasoning_id>
# =============================================================================


class TestApiReasoningDetail:
    @pytest.fixture
    def scene(self, db_session, make_org):
        org_a = make_org("c4-a")
        org_b = make_org("c4-b")
        user_a = _make_user(db_session, org_a)
        user_b = _make_user(db_session, org_b)
        solution_a = _make_solution(db_session, org_a)
        solution_b = _make_solution(db_session, org_b)
        reasoning_a = _make_reasoning_state(db_session, solution_a)
        db_session.commit()
        return {
            "org_a": org_a, "org_b": org_b,
            "user_a": user_a, "user_b": user_b,
            "solution_a": solution_a, "solution_b": solution_b,
            "reasoning_a": reasoning_a,
        }

    def _get(self, client, login_as, user, solution_id, reasoning_id):
        login_as(client, user)
        return client.get(f"/solutions/api/{solution_id}/reasoning/{reasoning_id}")

    def test_foreign_solution_is_refused(self, app, db_session, scene, client, login_as):
        with app.app_context():
            resp = self._get(
                client, login_as, scene["user_b"], scene["solution_a"].id, scene["reasoning_a"].id
            )

        assert resp.status_code == 404
        assert b"org-a-sentinel-reasoning" not in resp.data

    def test_own_solution_foreign_reasoning_is_refused(self, app, db_session, scene, client, login_as):
        with app.app_context():
            resp = self._get(
                client, login_as, scene["user_b"], scene["solution_b"].id, scene["reasoning_a"].id
            )

        assert resp.status_code == 404
        assert b"org-a-sentinel-reasoning" not in resp.data

    def test_foreign_and_nonexistent_solution_are_indistinguishable(
        self, app, db_session, scene, client, login_as
    ):
        with app.app_context():
            foreign = self._get(
                client, login_as, scene["user_b"], scene["solution_a"].id, scene["reasoning_a"].id
            )
            missing = self._get(
                client, login_as, scene["user_b"], NONEXISTENT_ID, scene["reasoning_a"].id
            )

        assert foreign.status_code == missing.status_code == 404
        assert _normalize(foreign.get_data(as_text=True)) == _normalize(
            missing.get_data(as_text=True)
        )

    def test_owner_can_read(self, app, db_session, scene, client, login_as):
        with app.app_context():
            resp = self._get(
                client, login_as, scene["user_a"], scene["solution_a"].id, scene["reasoning_a"].id
            )

        assert resp.status_code == 200
        assert resp.get_json()["context_snapshot"]["marker"] == "org-a-sentinel-reasoning"

    def test_no_active_organisation_is_refused(
        self, app, db_session, scene, client, login_as, monkeypatch
    ):
        _no_active_org(monkeypatch)
        with app.app_context():
            resp = self._get(
                client, login_as, scene["user_a"], scene["solution_a"].id, scene["reasoning_a"].id
            )

        assert resp.status_code == 404


# =============================================================================
# C5: GET /solutions/api/registry/specs/<spec_id>
# =============================================================================


class TestApiRegistryGetSpec:
    @pytest.fixture
    def scene(self, db_session, make_org):
        org_a = make_org("c5-a")
        org_b = make_org("c5-b")
        user_a = _make_user(db_session, org_a)
        user_b = _make_user(db_session, org_b)
        solution_a = _make_solution(db_session, org_a)
        spec_a = _make_spec(db_session, solution_a)
        db_session.commit()
        return {
            "org_a": org_a, "org_b": org_b,
            "user_a": user_a, "user_b": user_b,
            "solution_a": solution_a, "spec_a": spec_a,
        }

    def _get(self, client, login_as, user, spec_id):
        login_as(client, user)
        return client.get(f"/solutions/api/registry/specs/{spec_id}")

    def test_foreign_spec_is_refused(self, app, db_session, scene, client, login_as):
        with app.app_context():
            resp = self._get(client, login_as, scene["user_b"], scene["spec_a"].id)

        assert resp.status_code == 404
        assert b"org-a-sentinel-spec" not in resp.data

    def test_foreign_and_nonexistent_spec_are_indistinguishable(
        self, app, db_session, scene, client, login_as
    ):
        with app.app_context():
            foreign = self._get(client, login_as, scene["user_b"], scene["spec_a"].id)
            missing = self._get(client, login_as, scene["user_b"], NONEXISTENT_ID)

        assert foreign.status_code == missing.status_code == 404
        assert foreign.get_data(as_text=True) == missing.get_data(as_text=True)

    def test_owner_guard_passes_pre_existing_crash_unchanged(
        self, app, db_session, scene, client, login_as
    ):
        """The guard must let the owner's own spec through to the handler body.

        The handler body itself already raises on every caller, owner included
        (``spec.solution.solution_name`` — ``Solution`` carries no such
        attribute, a pre-existing defect outside this change's scope, same
        category as the sibling routes the security design left alone). This
        pins that the guard is not what raises here: an ``AttributeError``,
        not a 404, proving the guard passed the owner through exactly as
        before. The pre-existing crash is left alone rather than fixed or
        masked by this change.
        """
        with app.app_context(), pytest.raises(AttributeError):
            self._get(client, login_as, scene["user_a"], scene["spec_a"].id)

    def test_no_active_organisation_is_refused(
        self, app, db_session, scene, client, login_as, monkeypatch
    ):
        _no_active_org(monkeypatch)
        with app.app_context():
            resp = self._get(client, login_as, scene["user_a"], scene["spec_a"].id)

        assert resp.status_code == 404


# =============================================================================
# C6: GET /solutions/api/registry/specs/<spec_id>/openapi
# =============================================================================


class TestApiRegistryDownloadOpenapi:
    @pytest.fixture
    def scene(self, db_session, make_org):
        org_a = make_org("c6-a")
        org_b = make_org("c6-b")
        user_a = _make_user(db_session, org_a)
        user_b = _make_user(db_session, org_b)
        solution_a = _make_solution(db_session, org_a)
        spec_a = _make_spec(db_session, solution_a)
        db_session.commit()
        return {
            "org_a": org_a, "org_b": org_b,
            "user_a": user_a, "user_b": user_b,
            "solution_a": solution_a, "spec_a": spec_a,
        }

    def _get(self, client, login_as, user, spec_id):
        login_as(client, user)
        return client.get(f"/solutions/api/registry/specs/{spec_id}/openapi")

    def test_foreign_spec_is_refused_with_no_download_header(
        self, app, db_session, scene, client, login_as
    ):
        with app.app_context():
            resp = self._get(client, login_as, scene["user_b"], scene["spec_a"].id)

        assert resp.status_code == 404
        assert "Content-Disposition" not in resp.headers
        assert b"org-a-sentinel-spec" not in resp.data

    def test_foreign_and_nonexistent_spec_are_indistinguishable(
        self, app, db_session, scene, client, login_as
    ):
        with app.app_context():
            foreign = self._get(client, login_as, scene["user_b"], scene["spec_a"].id)
            missing = self._get(client, login_as, scene["user_b"], NONEXISTENT_ID)

        assert foreign.status_code == missing.status_code == 404
        assert foreign.get_data(as_text=True) == missing.get_data(as_text=True)

    def test_owner_can_download(self, app, db_session, scene, client, login_as):
        with app.app_context():
            resp = self._get(client, login_as, scene["user_a"], scene["spec_a"].id)

        assert resp.status_code == 200
        assert "attachment" in resp.headers.get("Content-Disposition", "")
        assert b"org-a-sentinel-spec" in resp.data

    def test_no_active_organisation_is_refused(
        self, app, db_session, scene, client, login_as, monkeypatch
    ):
        _no_active_org(monkeypatch)
        with app.app_context():
            resp = self._get(client, login_as, scene["user_a"], scene["spec_a"].id)

        assert resp.status_code == 404
        assert "Content-Disposition" not in resp.headers
