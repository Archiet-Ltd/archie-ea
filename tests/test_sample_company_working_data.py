"""The sample company shows a working answer, and removing it leaves nothing behind."""
import re

import pytest

from app import db


def _architect(org, email):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=email, first_name="Sam", last_name="Founder", organization_id=org.id,
        enterprise_role="enterprise_architect", confirmed=True,
    )
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "password"
    db.session.add(user)
    db.session.commit()
    return user


# Counted by organisation in SQL, so the figure does not depend on which
# organisation the last request left in the request context.
_TABLES = {
    "applications": "application_components",
    "owners": "application_owners",
    "mappings": "application_capability_mapping",
    "capabilities": "business_capability",
    "risks": "risks",
    "solutions": "solutions",
    "elements": "archimate_elements",
    "cost_facts": "cost_facts",
    "unified_capabilities": "unified_capabilities",
}


def _counts(org_id):
    return {
        key: db.session.execute(
            db.text("SELECT count(*) FROM %s WHERE organization_id = :o" % table), {"o": org_id}
        ).scalar()
        for key, table in _TABLES.items()
    }


def _figure(html, testid):
    match = re.search(r'data-testid="%s"[^>]*>\s*([^<]*?)\s*<' % testid, html)
    assert match, "figure %s not on the page" % testid
    return match.group(1)


def test_loading_the_sample_gives_applications_with_owners_costs_and_a_health_score(
    client, login_as, make_org
):
    org, other = make_org(), make_org()
    user = _architect(org, "founder-a@example.com")
    other_before = _counts(other.id)
    login_as(client, user)
    assert client.post("/architecture/sample-company/load").status_code == 302
    counts = _counts(org.id)
    assert 8 <= counts["applications"] <= 12
    assert counts["owners"] >= counts["applications"]
    assert counts["cost_facts"] >= counts["applications"]
    assert counts["mappings"] > 0 and counts["risks"] > 0
    rows = db.session.execute(
        db.text(
            "SELECT count(*) FROM application_components WHERE organization_id = :o "
            "AND total_cost_of_ownership > 0 AND lifecycle_status <> '' AND EXISTS ("
            "SELECT 1 FROM application_owners ao WHERE ao.application_id = application_components.id "
            "AND ao.ownership_type = 'primary')"
        ),
        {"o": org.id},
    ).scalar()
    assert rows == counts["applications"]

    login_as(client, user)
    page = client.get("/dashboard/overview").get_data(as_text=True)
    assert _figure(page, "health-kpi-applications") == str(counts["applications"])
    assert _figure(page, "health-score-value") not in ("", "—", "0")
    assert counts["solutions"] > 0
    assert _figure(page, "health-kpi-solutions") == str(counts["solutions"])
    assert _counts(other.id) == other_before


def test_removing_the_sample_returns_to_setup_and_leaves_nothing(client, login_as, make_org):
    org, other = make_org(), make_org()
    other_user = _architect(other, "founder-b@example.com")
    login_as(client, other_user)
    client.post("/architecture/sample-company/load")
    other_loaded = _counts(other.id)

    user = _architect(org, "founder-c@example.com")
    before = _counts(org.id)
    login_as(client, user)
    client.post("/architecture/sample-company/load")
    assert _counts(org.id)["applications"] > 0
    login_as(client, user)
    response = client.post("/architecture/sample-company/remove", follow_redirects=True)
    page = response.get_data(as_text=True)
    assert "Sample company removed" in page
    assert 'data-testid="guided-setup"' in page
    assert _counts(org.id) == before
    assert _counts(other.id) == other_loaded


def test_loading_twice_adds_the_working_data_once(client, login_as, make_org):
    org = make_org()
    user = _architect(org, "founder-d@example.com")
    login_as(client, user)
    client.post("/architecture/sample-company/load")
    first = _counts(org.id)
    assert first["applications"] > 0
    login_as(client, user)
    client.post("/architecture/sample-company/load")
    assert _counts(org.id) == first
