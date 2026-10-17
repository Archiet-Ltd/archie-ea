"""Plain errors and empty states.

/intelligence/history/changes answers a normal page (not a raw JSON 400) when
called without parameters, and the Investment Map, Investment Priorities and
compliance frameworks dashboards show "No data yet" with a next step instead
of a bare zero or "Setup Required" with no explanation.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.business_capabilities import BusinessCapability
from app.models.compliance_models import RegulatoryFramework
from app.models.unified_application_capability_mapping import (
    UnifiedApplicationCapabilityMapping,
)

HISTORY_CHANGES = "/intelligence/history/changes"
INVESTMENT_MAP = "/enterprise/strategic/investment-matrix"
INVESTMENT_PRIORITIES = "/architecture/investment-priorities"
COMPLIANCE_FRAMEWORKS = "/dashboard/compliance"

ORG_LABELS = ["first", "second"]


def _user(db_session, org_id):
    from app.models.user import User

    user = User(
        email=f"plain-empty-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Plain",
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _assert_block(html, next_step_fragment):
    assert "No data yet" in html or "Next step:" in html
    assert "Next step: %s" % next_step_fragment in html
    assert "Who can do it:" in html
    assert "Setup Required" not in html


# --- /intelligence/history/changes: a page, never raw JSON 400 --------------


@pytest.mark.parametrize("org_label", ORG_LABELS)
def test_history_changes_without_parameters_answers_a_normal_page(
    app, db_session, make_org, client, login_as, org_label
):
    org = make_org(org_label)
    user = _user(db_session, org.id)
    login_as(client, user)
    response = client.get(HISTORY_CHANGES)
    assert response.status_code == 200
    assert response.content_type.startswith("text/html")
    html = response.get_data(as_text=True)
    # The missing-parameter error is shown as plain words in the shared block,
    # never as the raw JSON error envelope the API still returns.
    assert "Next step: Provide both" in html
    assert "YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS" in html
    assert "from is required" in html
    assert "MISSING_PARAMETER" not in html
    assert '"error"' not in html


# --- Investment Map ---------------------------------------------------------


@pytest.mark.parametrize("org_label", ORG_LABELS)
def test_investment_map_empty_shows_no_data_yet_with_next_step(
    app, db_session, make_org, client, login_as, org_label
):
    org = make_org(org_label)
    user = _user(db_session, org.id)
    login_as(client, user)
    # Capabilities are tenant-scoped: a fresh organisation has none.
    assert BusinessCapability.query.filter_by(organization_id=org.id).count() == 0
    response = client.get(INVESTMENT_MAP)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    _assert_block(html, "Load capability data via the import tool or API.")
    assert "Who can do it: Platform administrators or data stewards" in html


# --- Investment Priorities --------------------------------------------------


@pytest.mark.parametrize("org_label", ORG_LABELS)
def test_investment_priorities_empty_shows_no_data_yet_with_next_step(
    app, db_session, make_org, client, login_as, org_label
):
    org = make_org(org_label)
    user = _user(db_session, org.id)
    login_as(client, user)
    # Capability mappings are global (not tenant-scoped): clear them inside
    # this transaction so the empty state is deterministic whatever other
    # tests committed to the shared database; the rollback fixture undoes it.
    db_session.query(UnifiedApplicationCapabilityMapping).delete()
    db_session.flush()
    response = client.get(INVESTMENT_PRIORITIES)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    _assert_block(html, "Map applications to capabilities first — the dashboard unlocks once the first mapping is saved.")
    assert "Who can do it: Architects or system administrators" in html


# --- Compliance frameworks dashboard ----------------------------------------


@pytest.mark.parametrize("org_label", ORG_LABELS)
def test_compliance_frameworks_dashboard_empty_shows_no_data_yet_with_next_step(
    app, db_session, make_org, client, login_as, org_label
):
    org = make_org(org_label)
    user = _user(db_session, org.id)
    login_as(client, user)
    # Frameworks are global master data: clear them inside this transaction
    # for the same determinism reason (rolled back at teardown).
    db_session.query(RegulatoryFramework).delete()
    db_session.flush()
    response = client.get(COMPLIANCE_FRAMEWORKS)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "No regulatory frameworks have been configured yet" in html
    assert "Next step: Add frameworks via the compliance administration panel." in html
    assert "Who can do it: Compliance administrators or system administrators" in html
    assert "Setup Required" not in html
