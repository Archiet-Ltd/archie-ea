"""The connector allowlist gate must refuse a non-permitted or unrecognised
connector type before any credential is stored or any outbound workflow is
created -- not just log and continue.

Before this fix, ConnectorOrchestrator.create_sync_workflow stored
credentials in the vault unconditionally, then handed any connector_type
it did not recognise to a generic HTTP-request workflow builder that
turned a caller-supplied base_url into a scheduled outbound fetch.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
def org(make_org):
    return make_org("allowlist-gate")


def _credential_row_count(db_session, solution_id):
    from app.modules.codegen.services.credential_vault import ConnectorCredential

    return ConnectorCredential.query.filter_by(solution_id=solution_id).count()


class TestServiceLevelGate:
    """The boundary that holds for any caller, whether or not it checked first."""

    def test_unrecognised_connector_type_is_refused_before_storing_anything(
        self, app, db_session, org
    ):
        from app.modules.codegen.services.connector_orchestrator import ConnectorOrchestrator
        from app.modules.intelligence.services.connector_allowlist import ConnectorNotPermitted

        solution_id = uuid.uuid4().int % 1_000_000

        with pytest.raises(ConnectorNotPermitted):
            ConnectorOrchestrator().create_sync_workflow(
                solution_id=solution_id,
                connector_type="totally-unrecognised-type",
                credentials={"base_url": "https://attacker-controlled.invalid"},
                object_mappings={},
                target_api_url="https://archie.invalid",
            )

        assert _credential_row_count(db_session, solution_id) == 0, (
            "A refused connector type must not leave a stored credential behind."
        )

    def test_recognised_but_non_permitted_connector_type_is_refused(
        self, app, db_session, org
    ):
        """salesforce has a real workflow builder registered but is not on the
        allowlist -- it must still be refused, not silently wired up."""
        from app.modules.codegen.services.connector_orchestrator import ConnectorOrchestrator
        from app.modules.intelligence.services.connector_allowlist import ConnectorNotPermitted

        solution_id = uuid.uuid4().int % 1_000_000

        with pytest.raises(ConnectorNotPermitted):
            ConnectorOrchestrator().create_sync_workflow(
                solution_id=solution_id,
                connector_type="salesforce",
                credentials={"base_url": "https://example.invalid"},
                object_mappings={},
                target_api_url="https://archie.invalid",
            )

        assert _credential_row_count(db_session, solution_id) == 0

    def test_permitted_connector_type_still_reaches_vault_store(
        self, app, db_session, org, monkeypatch
    ):
        """A refusal for everything else must not be a refusal for everything --
        the gate must still let an allowlisted type through to storage."""
        from app.modules.codegen.services import connector_orchestrator as orch_module

        def _fake_n8n_request(self, method, path, **kwargs):
            class _Resp:
                status_code = 201
                def json(self):
                    return {"id": "wf-1", "webhookUrl": None}
            return _Resp()

        monkeypatch.setattr(
            orch_module.ConnectorOrchestrator, "_n8n_request", _fake_n8n_request
        )

        import app.utils.ssrf_guard as guard

        monkeypatch.setattr(
            guard.socket,
            "getaddrinfo",
            lambda host, port, *a, **k: [(2, 1, 6, "", ("93.184.216.34", port))],
        )
        monkeypatch.setattr(guard, "_local_interface_addresses", lambda: set())

        from app.models.solution_models import Solution

        solution = Solution(name="Allowlist gate test solution", organization_id=org.id)
        db_session.add(solution)
        db_session.flush()
        solution_id = solution.id

        connector = orch_module.ConnectorOrchestrator().create_sync_workflow(
            solution_id=solution_id,
            connector_type="jira",
            credentials={"base_url": "https://example.atlassian.net"},
            object_mappings={},
            target_api_url="https://archie.invalid",
        )

        assert connector is not None
        assert _credential_row_count(db_session, solution_id) == 1, (
            "A permitted connector type must still reach vault.store."
        )


class TestConnectionTestIsGated:
    """test_connection's rest_api branch makes a live outbound request from
    the server for a caller-supplied base_url -- it must be gated the same
    as create_sync_workflow, not just the create path."""

    def test_rest_api_connection_test_is_refused_before_any_outbound_request(
        self, monkeypatch
    ):
        from app.modules.codegen.services.connector_orchestrator import ConnectorOrchestrator

        def _fail_if_called(*args, **kwargs):
            raise AssertionError(
                "requests.get must not run for a connector type the allowlist refuses"
            )

        monkeypatch.setattr(
            "app.modules.codegen.services.connector_orchestrator.requests.get",
            _fail_if_called,
        )

        result = ConnectorOrchestrator().test_connection(
            "rest_api", {"base_url": "https://attacker-controlled.invalid"}
        )

        assert result["success"] is False


class TestRouteLevelGate:
    """The route call gives a clean 403 -- a normal caller never reaches
    the service-level exception."""

    def test_create_connector_route_refuses_non_permitted_type_with_403(
        self, app, db_session, org, client, login_as
    ):
        from app.models.solution_models import Solution
        from app.models.user import User

        solution = Solution(name="Route gate test solution", organization_id=org.id)
        db_session.add(solution)
        db_session.flush()

        admin = User(
            email=f"routegate-{uuid.uuid4().hex[:8]}@example.com",
            first_name="Route",
            last_name="Gate",
            organization_id=org.id,
            confirmed=True,
            enterprise_role="platform_administrator",
            is_platform_admin=True,
        )
        db_session.add(admin)
        db_session.flush()

        login_as(client, admin)
        resp = client.post(
            f"/solutions/{solution.id}/codegen/connectors",
            json={
                "connector_type": "salesforce",
                "credentials": {"base_url": "https://example.invalid"},
            },
        )

        assert resp.status_code == 403, (
            f"Expected a clean 403 for a non-permitted connector type, got {resp.status_code}"
        )
        assert _credential_row_count(db_session, solution.id) == 0
