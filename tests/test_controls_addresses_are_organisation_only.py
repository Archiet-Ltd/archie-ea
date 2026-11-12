"""The addresses under /policy-monitoring/ return only the asking organisation's data, or nothing.

The status, compliance, violations, alert, test and health addresses read a history held in the
server's memory for the whole process, which no organisation owns. Nothing on any page reads them, so
they answer "not found". The Controls page itself is the one place a person reads this subject, and it
shows only the asking organisation's checks. A person without the role gets the refusal page.
"""

import uuid
from datetime import datetime

import pytest

from tests.test_controls_show_checked_changes import CONTROLS, _viewer
from tests.test_sidebar_offers_only_permitted_pages import _ordinary_person

RETIRED_READS = ("status", "compliance", "violations", "alert", "health")


@pytest.fixture
def organisation_a_history():
    """One entry in the process-wide history, as if organisation A's work had been recorded."""
    from app.utils.policy_tool_wrapper import policy_tool_wrapper

    marker = "alpha-secret-%s" % uuid.uuid4().hex[:8]
    entry = {
        "tool_name": "write_to_file",
        "file_path": "/work/%s/plan.md" % marker,
        "operation": "create",
        "context": "Notes for %s" % marker,
        "timestamp": datetime.utcnow(),
        "validated": True,
    }
    policy_tool_wrapper.operation_history.append(entry)
    yield marker
    policy_tool_wrapper.operation_history[:] = [e for e in policy_tool_wrapper.operation_history if e is not entry]


def _architect_of(db_session, make_org, login_as, client, label):
    org = make_org(label)
    login_as(client, _viewer(db_session, org))
    return org


@pytest.mark.parametrize("address", RETIRED_READS)
def test_organisation_b_gets_nothing_of_organisation_a_from_the_old_addresses(
    app, db_session, make_org, client, login_as, organisation_a_history, address
):
    make_org("policy-a")
    _architect_of(db_session, make_org, login_as, client, "policy-b")

    response = client.get("/policy-monitoring/%s" % address)

    assert response.status_code == 404
    assert organisation_a_history not in response.get_data(as_text=True)


def test_organisation_b_cannot_run_the_test_address_either(app, db_session, make_org, client, login_as):
    _architect_of(db_session, make_org, login_as, client, "policy-b-test")

    response = client.post("/policy-monitoring/test", json={"test_type": "documentation"})

    assert response.status_code == 404


def test_the_controls_page_still_opens_for_an_architect_and_carries_nothing_from_the_history(
    app, db_session, make_org, client, login_as, organisation_a_history
):
    _architect_of(db_session, make_org, login_as, client, "policy-b-page")

    response = client.get(CONTROLS)

    assert response.status_code == 200
    assert organisation_a_history not in response.get_data(as_text=True)


def test_a_person_without_the_role_gets_the_refusal_page(app, db_session, make_org):
    org = make_org("policy-refused")
    client = _ordinary_person(app, db_session, make_org, "application_manager", "controls-refused", org)

    response = client.get(CONTROLS)

    assert response.status_code == 403
    html = response.get_data(as_text=True)
    assert "You don't have access to this page" in html or "You don&#39;t have access to this page" in html
    assert 'data-testid="policy-checked-value"' not in html


@pytest.mark.parametrize("address", ("/api/policy-monitoring/violations", "/enterprise/api/policy-monitoring/violations"))
def test_organisation_b_gets_nothing_of_organisation_a_violations_from_the_json_address(
    app, db_session, make_org, client, login_as, address
):
    from app.models.policy_monitoring import ArchitecturePolicy, PolicyViolation

    org_a = make_org("policy-json-a")
    marker = "alpha-app-%s" % uuid.uuid4().hex[:8]
    policy = ArchitecturePolicy(name="Encrypt at rest", organization_id=org_a.id)
    db_session.add(policy)
    db_session.flush()
    db_session.add(
        PolicyViolation(
            policy_id=policy.id, entity_type="application", entity_id=1, entity_name=marker,
            violation_details="Details for %s" % marker, severity="high", status="open",
            organization_id=org_a.id,
        )
    )
    db_session.commit()
    _architect_of(db_session, make_org, login_as, client, "policy-json-b")

    response = client.get(address)

    assert marker not in response.get_data(as_text=True)
    assert response.status_code == 404
