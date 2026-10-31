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


def test_loading_the_sample_gives_applications_with_owners_and_costs(
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
    welcome = re.search(r'data-testid="welcome-applications"[^>]*>\s*<span[^>]*>\s*(\d+)\s*<', page)
    assert welcome, "the welcome card does not show the applications"
    assert welcome.group(1) == str(counts["applications"])
    assert counts["solutions"] > 0
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
    assert "Connect GitHub and your model is built for you" in page
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


def _accept_first_suggestion(org_id):
    """Link the organisation's first application to its first capability, as accepting a suggestion does."""
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping

    app_id = db.session.execute(
        db.text("SELECT id FROM application_components WHERE organization_id = :o ORDER BY id LIMIT 1"),
        {"o": org_id},
    ).scalar()
    capability_id = db.session.execute(
        db.text("SELECT id FROM unified_capabilities WHERE organization_id = :o ORDER BY id LIMIT 1"),
        {"o": org_id},
    ).scalar()
    db.session.add(UnifiedApplicationCapabilityMapping(
        unified_capability_id=capability_id, application_component_id=app_id,
        support_level="full", relationship_type="supports",
    ))
    db.session.commit()
    return app_id


def _links_of(org_id):
    return db.session.execute(
        db.text(
            "SELECT count(*) FROM unified_application_capability_mapping m "
            "JOIN application_components a ON a.id = m.application_component_id WHERE a.organization_id = :o"
        ),
        {"o": org_id},
    ).scalar()


def test_removing_the_sample_after_accepting_a_suggested_capability(client, login_as, make_org):
    org, other = make_org(), make_org()
    user = _architect(org, "founder-e@example.com")
    other_user = _architect(other, "founder-f@example.com")
    before = _counts(org.id)

    # the second organisation has its own sample, its own data and its own accepted link
    login_as(client, other_user)
    client.post("/architecture/sample-company/load")
    _accept_first_suggestion(other.id)
    other_loaded = _counts(other.id)
    other_links = _links_of(other.id)
    assert other_loaded["applications"] > 0 and other_links > 0

    login_as(client, user)
    client.post("/architecture/sample-company/load")
    app_id = _accept_first_suggestion(org.id)
    assert _links_of(org.id) > 0

    login_as(client, user)
    response = client.post("/architecture/sample-company/remove", follow_redirects=True)
    assert response.status_code == 200
    assert "Sample company removed" in response.get_data(as_text=True)
    assert _counts(org.id) == before
    assert _links_of(org.id) == 0
    left = db.session.execute(
        db.text("SELECT count(*) FROM unified_application_capability_mapping WHERE application_component_id = :a"),
        {"a": app_id},
    ).scalar()
    assert left == 0
    # the second organisation still has all of its own
    assert _counts(other.id) == other_loaded
    assert _links_of(other.id) == other_links
