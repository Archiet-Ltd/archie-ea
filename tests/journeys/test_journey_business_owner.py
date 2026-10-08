"""Journeys for the business_owner role, added 1 Oct 2026.

Self-serve sign-ups used to land on `platform_admin`: a 28-link sidebar of
specialist and administration links and an operations-steward AI voice, for a
founder who does several jobs and wants plain answers about cost, risk and
change. `business_owner` is a VIEW, not a new authority: it holds exactly what
platform_admin holds (ADMINISTRATOR_ROLES in app/models/user.py) and differs in
a short sidebar and a plain-language AI persona. These pin both halves -- that
the view is small, and that nothing is lost behind it.
"""

import uuid

import pytest

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey


def test_the_role_is_assignable_with_a_governed_plain_language_charter(app):
    from app.models.user import ADMINISTRATOR_ROLES, ROLE_DISPLAY_NAMES, VALID_ROLES
    from app.modules.ai_chat.services.architect_persona_charters import (
        build_architect_prompt,
        get_default_chat_persona,
    )

    assert "business_owner" in VALID_ROLES
    assert "business_owner" in ADMINISTRATOR_ROLES
    assert ROLE_DISPLAY_NAMES["business_owner"] == "Business Owner"
    assert ROLE_DISPLAY_NAMES["platform_admin"] == "Administrator"
    assert get_default_chat_persona("business_owner") == "business_owner"
    prompt = build_architect_prompt("business_owner")
    assert prompt, "business_owner has no charter"
    assert "NO FABRICATION" in prompt


def test_the_business_owner_sidebar_is_shorter_than_the_administrator_sidebar(app):
    from app import db
    from app.models.user import User
    from app.utils.role_access import get_sidebar_zones

    with app.app_context():
        org_id = make_org(db, "OwnerNav")
        owner_id = make_user(db, org_id, "owner", enterprise_role="business_owner",
                             role_name="Administrator")
        admin_id = make_user(db, org_id, "admin", enterprise_role="platform_admin",
                             role_name="Administrator")

        def labels(user_id):
            user = db.session.get(User, user_id)
            return [link["label"] for zone in get_sidebar_zones(user)
                    for link in zone["links"]]

        owner, admin = labels(owner_id), labels(admin_id)

    for expected in ("Twin Map", "Applications", "Risk Register", "Health Scorecard"):
        assert expected in owner, "%r missing from %s" % (expected, owner)
    assert len(owner) < len(admin), (len(owner), len(admin))


def test_the_business_owner_holds_every_section_the_administrator_holds(app):
    from app.utils.role_access import ROLE_SECTION_ACCESS, can_access_section

    assert ROLE_SECTION_ACCESS["business_owner"] == ROLE_SECTION_ACCESS["platform_admin"]

    class _Owner:
        enterprise_role = "business_owner"

    for section in ROLE_SECTION_ACCESS["platform_admin"]:
        assert can_access_section(_Owner(), section), section


def test_requires_role_admits_a_business_owner_where_it_admits_an_administrator(app, client):
    """A smaller sidebar must not put a page behind a 403: the procurement list is
    guarded by requires_role(['procurement', 'portfolio_manager']), which admits
    the administrator roles automatically."""
    from app import db

    with app.app_context():
        org_id = make_org(db, "OwnerGuard")
        owner_id = make_user(db, org_id, "ownerguard", enterprise_role="business_owner",
                             role_name="Administrator")

    login(client, owner_id)
    response = client.get("/procurement/contracts")
    assert response.status_code == 200, response.status_code


def test_a_business_owner_records_a_capability_the_estate_can_use(app, client):
    from app import db
    from app.models.business_capabilities import BusinessCapability

    with app.app_context():
        org_id = make_org(db, "OwnerWrite")
        owner_id = make_user(db, org_id, "ownerwrite", enterprise_role="business_owner",
                             role_name="Administrator")

    login(client, owner_id)
    name = "Customer Support %s" % uuid.uuid4().hex[:8]
    response = client.post(
        "/enterprise/capabilities",
        json={"name": name, "type": "operational",
              "description": "Answers customers.", "level": 1},
    )
    assert response.status_code == 201, response.data[:300]

    with app.app_context():
        db.session.expunge_all()
        row = db.session.execute(
            db.select(BusinessCapability).filter_by(name=name)
        ).scalar_one()
        assert row.organization_id == org_id

    page = client.get("/enterprise/capabilities")
    assert page.status_code == 200
    assert name in page.get_data(as_text=True)


def test_self_serve_registration_creates_a_business_owner_who_is_an_org_admin(app, client):
    """Authority comes from grant_org_admin(), not from the persona."""
    from app.models.user import User

    email = "owner-%s@example.com" % uuid.uuid4().hex[:8]
    response = client.post("/account/register", data={
        "first_name": "Sign", "last_name": "Up", "email": email,
        "password": "Fixture-only-1!", "password2": "Fixture-only-1!",
    })
    assert response.status_code in (200, 302), response.status_code

    with app.app_context():
        user = User.query.filter_by(email=email).first()
        assert user is not None, "registration created no user"
        assert user.enterprise_role == "business_owner"
        assert user.is_org_admin is True
