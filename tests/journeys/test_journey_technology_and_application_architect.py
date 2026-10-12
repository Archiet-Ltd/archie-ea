"""Journeys for the two personas promoted from charter-only on 1 Oct 2026.

`technology_architect` and `application_architect` both had AI charters and a
place in the architecture-journey member roles, and no user could ever be
either. The owner decided to promote them because the release plan serves both
at the paid launch.

A promoted persona has to be able to do its job, which is what these assert:
they can be assigned, they have their own sidebar, the pages that justify the
role serve for them rather than 403ing a role nobody added to the guards, they
are fenced from the platform admin surface, and they can perform a governed
write that persists. The capability write is the one endpoint already proven for
an architect role; it shows the new roles are not locked out by a guard that
predates them, not that it is the whole of either persona's job.
"""

import uuid

import pytest

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey

NEW_ROLES = ("technology_architect", "application_architect")


@pytest.mark.parametrize("role", NEW_ROLES)
def test_the_role_can_be_assigned_and_resolves_to_its_own_surfaces(app, role):
    from app.models.user import ROLE_DISPLAY_NAMES, VALID_ROLES
    from app.modules.ai_chat.services.architect_persona_charters import (
        build_architect_prompt,
        get_default_chat_persona,
    )

    assert role in VALID_ROLES
    assert ROLE_DISPLAY_NAMES[role]
    assert get_default_chat_persona(role) == role
    prompt = build_architect_prompt(role)
    assert prompt, "%s has no charter" % role
    assert "NO FABRICATION" in prompt


@pytest.mark.parametrize("role", NEW_ROLES)
def test_the_sidebar_reaches_the_persona_own_surfaces(app, role):
    """A persona whose pages are reachable only by URL is not a persona."""
    from app import db
    from app.models.user import User
    from app.utils.role_access import get_sidebar_zones

    with app.app_context():
        org_id = make_org(db, "Nav-" + role)
        user_id = make_user(db, org_id, role[:4], enterprise_role=role,
                            role_name="Architect")
        user = db.session.get(User, user_id)
        labels = {
            link["label"]
            for zone in get_sidebar_zones(user)
            for link in zone["links"]
        }

    for expected in ("Applications", "Architecture Model", "Interface Register"):
        assert expected in labels, (
            "%r is missing from the %s sidebar: %s" % (expected, role, sorted(labels))
        )
    assert "Tech Radar" in labels or role == "application_architect"
    # The administrator surface is not theirs.
    assert "Billing" not in labels


@pytest.mark.parametrize("role", NEW_ROLES)
def test_the_pages_behind_the_sidebar_serve_for_the_role(app, client, role):
    from app import db

    with app.app_context():
        org_id = make_org(db, "Pages-" + role)
        user_id = make_user(db, org_id, role[:4], enterprise_role=role,
                            role_name="Architect")

    login(client, user_id)
    for path in ("/applications/", "/interface-register/", "/technology/radar/"):
        response = client.get(path)
        assert response.status_code == 200, (
            "%s returned %s for a %s" % (path, response.status_code, role)
        )


@pytest.mark.parametrize("role", NEW_ROLES)
def test_the_role_is_fenced_from_the_platform_admin_surface(app, role):
    from app.utils.role_access import ROLE_SECTION_ACCESS

    sections = ROLE_SECTION_ACCESS[role]
    for forbidden in ("administration", "procurement", "my_applications"):
        assert forbidden not in sections, "%s reaches %r" % (role, forbidden)
    assert "data_integration" in sections


def _record_capability(app, client, role):
    """Sign in as *role*, record a capability, return (name, org_id, status)."""
    from app import db

    with app.app_context():
        org_id = make_org(db, "Write-" + role)
        user_id = make_user(db, org_id, role[:4], enterprise_role=role,
                            role_name="Architect")

    login(client, user_id)
    name = "Payments Platform %s" % uuid.uuid4().hex[:8]
    response = client.post(
        "/enterprise/capabilities",
        json={"name": name, "type": "operational",
              "description": "Runs card and bank payments.", "level": 1},
    )
    return name, org_id, response


def _assert_persisted_and_visible(app, client, name, org_id):
    from app import db
    from app.models.business_capabilities import BusinessCapability

    with app.app_context():
        db.session.expunge_all()
        row = db.session.execute(
            db.select(BusinessCapability).filter_by(name=name)
        ).scalar_one()
        assert row.organization_id == org_id

    listing = client.get("/enterprise/capabilities")
    assert listing.status_code == 200
    assert name in listing.get_data(as_text=True)


def test_a_technology_architect_records_a_capability_the_estate_can_use(app, client):
    name, org_id, response = _record_capability(app, client, "technology_architect")
    assert response.status_code == 201, response.data[:300]
    _assert_persisted_and_visible(app, client, name, org_id)


def test_an_application_architect_records_a_capability_the_estate_can_use(app, client):
    name, org_id, response = _record_capability(app, client, "application_architect")
    assert response.status_code == 201, response.data[:300]
    _assert_persisted_and_visible(app, client, name, org_id)
