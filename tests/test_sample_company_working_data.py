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
    # Owners are labelled names on the application (lead's ruling, 30 Oct 2026): no owner account is
    # created, so the count of owner records is 0 and each application carries a "(sample owner)" name.
    assert counts["owners"] == 0
    assert counts["cost_facts"] >= counts["applications"]
    assert counts["mappings"] > 0 and counts["risks"] > 0
    rows = db.session.execute(
        db.text(
            "SELECT count(*) FROM application_components WHERE organization_id = :o "
            "AND total_cost_of_ownership > 0 AND lifecycle_status <> '' "
            "AND application_owner LIKE '% (sample owner)' AND business_owner LIKE '% (sample owner)'"
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


def test_removing_the_sample_after_accepting_a_suggested_capability(client, login_as, make_org):
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping

    org, other = make_org(), make_org()
    user = _architect(org, "founder-e@example.com")
    before = _counts(org.id)
    login_as(client, user)
    client.post("/architecture/sample-company/load")
    app_id = db.session.execute(
        db.text("SELECT id FROM application_components WHERE organization_id = :o ORDER BY id LIMIT 1"),
        {"o": org.id},
    ).scalar()
    capability_id = db.session.execute(
        db.text("SELECT id FROM unified_capabilities WHERE organization_id = :o ORDER BY id LIMIT 1"),
        {"o": org.id},
    ).scalar()
    db.session.add(UnifiedApplicationCapabilityMapping(
        unified_capability_id=capability_id, application_component_id=app_id,
        support_level="full", relationship_type="supports",
    ))
    db.session.commit()

    login_as(client, user)
    response = client.post("/architecture/sample-company/remove", follow_redirects=True)
    assert response.status_code == 200
    assert "Sample company removed" in response.get_data(as_text=True)
    assert _counts(org.id) == before
    left = db.session.execute(
        db.text("SELECT count(*) FROM unified_application_capability_mapping WHERE application_component_id = :a"),
        {"a": app_id},
    ).scalar()
    assert left == 0


def test_sample_records_carry_the_sample_origin_never_a_connector(client, login_as, make_org):
    """A sample record is labelled sample data, so it cannot be shown as evidence a connector read."""
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.modules.intelligence.services.query_service import _row_origin

    org = make_org()
    user = _architect(org, "founder-e@example.com")
    login_as(client, user)
    assert client.post("/architecture/sample-company/load").status_code == 302
    kinds = {
        row[0] for row in db.session.execute(
            db.text(
                "SELECT source_kind FROM archimate_elements WHERE organization_id = :o AND source_label = 'Sample company' "
                "UNION ALL SELECT source_kind FROM archimate_relationships "
                "WHERE organization_id = :o AND source_label = 'Sample company'"
            ),
            {"o": org.id},
        )
    }
    # Elements the file itself brought in keep the importer's own "import" kind; what the loader adds is "sample".
    assert "sample" in kinds and "connector" not in kinds
    assert not db.session.execute(
        db.text("SELECT 1 FROM archimate_relationships WHERE organization_id = :o AND source_kind = 'connector'"),
        {"o": org.id},
    ).first()
    rel = ArchiMateRelationship.query.filter_by(organization_id=org.id, source_kind="sample").first()
    assert rel is not None
    assert _row_origin(rel)["text"] == "Sample data"
    assert ArchiMateElement.query.filter_by(organization_id=org.id, source_kind="sample").count() > 0


def test_sample_owners_are_labelled_names_never_accounts(client, login_as, make_org):
    """Sample owners are plain text "<name> (sample owner)": no user account, no seat, never the loader.

    Changed from asserting three sample owner accounts, on the lead's ruling of 30 Oct 2026: the
    sample creates no user rows at all.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.models.user import User

    org = make_org()
    user = _architect(org, "founder-f@example.com")
    login_as(client, user)
    users_before = User.query.filter_by(organization_id=org.id).count()
    client.post("/architecture/sample-company/load")
    db.session.expire_all()
    assert User.query.filter_by(organization_id=org.id).count() == users_before
    assert not db.session.execute(
        db.text("SELECT 1 FROM users WHERE email LIKE '%@sample.invalid'")
    ).first()
    apps = ApplicationComponent.query.filter(
        ApplicationComponent.organization_id == org.id, ApplicationComponent.application_code.like("SAMPLE-%")
    ).all()
    assert len(apps) >= 10
    loader = " ".join(p for p in (user.first_name, user.last_name) if p) or user.email
    for app in apps:
        for owner in (app.business_owner, app.application_owner):
            assert owner and owner.endswith(" (sample owner)")
            assert loader not in owner
    assert not db.session.execute(
        db.text("SELECT 1 FROM application_owners WHERE organization_id = :o"), {"o": org.id}
    ).first()
    from app.services.billing_plans import _count_members

    assert _count_members(db.session.connection(), org.id, "all") == 1
