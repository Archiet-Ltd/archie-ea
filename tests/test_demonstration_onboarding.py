"""A prospect's route into the labelled demonstration organisation and into a trial.

Every test runs inside the rolled-back ``db_session`` transaction, so nothing
it creates survives the test.
"""

from __future__ import annotations

import uuid

import pytest

from app.services import demonstration_service as demo


@pytest.fixture
def demo_org(db_session):
    """The demonstration organisation with its read-only viewer, as the seeder leaves it."""
    from app.models.organization import Organization
    from app.models.user import Role, User

    existing = Organization.query.filter_by(slug=demo.DEMO_SLUG).first()
    if existing is not None:
        # A seeded database: flag it inside this transaction only.
        demo.mark_demonstration(existing)
        db_session.flush()
        viewer = User.query.filter_by(
            email=demo.DEMO_USER_EMAIL, organization_id=existing.id
        ).first()
        if viewer is not None:
            return existing, viewer
        org = existing
    else:
        org = Organization(name="Lantern Quay Systems", slug=demo.DEMO_SLUG)
        db_session.add(org)
        db_session.flush()
        demo.mark_demonstration(org)
    Role.insert_roles()
    viewer = User(
        email=demo.DEMO_USER_EMAIL,
        first_name="Demo",
        last_name="User",
        organization_id=org.id,
        confirmed=True,
        role=Role.query.filter_by(name="Viewer").first(),
    )
    db_session.add(viewer)
    db_session.flush()
    return org, viewer


@pytest.fixture
def real_user(db_session, make_org):
    from app.models.user import Role, User

    org = make_org("real")
    user = User(
        email=f"real-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Real",
        last_name="Person",
        organization_id=org.id,
        confirmed=True,
        role=Role.query.filter_by(name="Administrator").first(),
        is_org_admin=True,
    )
    db_session.add(user)
    db_session.flush()
    return org, user


# ── the flag and the seeder ────────────────────────────────────────────────


def test_flag_is_on_the_organisation_and_only_there(db_session, make_org, demo_org):
    org, _ = demo_org
    other = make_org("plain")
    assert demo.is_demonstration(org)
    assert not demo.is_demonstration(other)
    assert demo.is_demonstration_org_id(org.id)
    assert not demo.is_demonstration_org_id(other.id)
    assert not demo.is_demonstration_org_id(None)


def test_seeder_flags_the_organisation_it_creates(db_session):
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.organization import Organization

    seed_demo_company()
    org = Organization.query.filter_by(slug=demo.DEMO_SLUG).first()
    assert org is not None and demo.is_demonstration(org)
    assert demo.demonstration_viewer() is not None


# ── the entry ──────────────────────────────────────────────────────────────


def test_entry_page_is_public_and_says_the_data_is_invented(client, demo_org):
    resp = client.get("/demonstration")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "demonstration-enter" in text
    assert "invented" in text


def test_entry_page_says_so_when_the_demonstration_is_missing(client, db_session):
    from app.models.organization import Organization

    Organization.query.filter_by(slug=demo.DEMO_SLUG).delete()
    db_session.flush()
    resp = client.get("/demonstration")
    text = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "demonstration-unavailable" in text
    assert "demonstration-enter" not in text
    post = client.post("/demonstration")
    assert post.status_code == 200
    assert "demonstration-unavailable" in post.get_data(as_text=True)


def test_pricing_page_offers_the_demonstration(client):
    resp = client.get("/pricing")
    assert resp.status_code == 200
    assert 'href="/demonstration"' in resp.get_data(as_text=True)


def test_demo_viewer_sees_the_banner_on_the_composer(client, demo_org):
    client.post("/demonstration")
    text = client.get("/archimate/composer").get_data(as_text=True)
    assert 'data-testid="demonstration-banner"' in text
    assert text.count('data-testid="demonstration-banner"') == 1


def test_entering_signs_in_as_the_viewer_and_every_screen_is_labelled(
    client, demo_org
):
    org, viewer = demo_org
    resp = client.post("/demonstration")
    assert resp.status_code == 302
    page = client.get(resp.headers["Location"])
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert 'data-testid="demonstration-banner"' in text
    assert "not yours" in text
    assert "demonstration-banner-trial" in text


def test_a_real_organisation_never_sees_the_banner(client, login_as, real_user):
    _, user = real_user
    login_as(client, user)
    text = client.get("/dashboard/").get_data(as_text=True)
    assert "demonstration-banner" not in text

    composer_text = client.get("/archimate/composer").get_data(as_text=True)
    assert "demonstration-banner" not in composer_text


def test_a_signed_in_real_user_keeps_their_own_session(client, login_as, demo_org, real_user):
    _, user = real_user
    login_as(client, user)
    resp = client.post("/demonstration")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "demonstration-signed-in" in text
    assert "demonstration-start-trial" not in text
    text = client.get("/dashboard/").get_data(as_text=True)
    assert "demonstration-banner" not in text


# ── the trial ──────────────────────────────────────────────────────────────

_FORM = {
    "first_name": "Trial",
    "last_name": "Starter",
    "password": "correct-horse-battery-1A",
    "password2": "correct-horse-battery-1A",
}


def _sign_up_trial(client):
    email = f"trial-{uuid.uuid4().hex[:8]}@example.com"
    return email, client.post("/demonstration/trial", data={**_FORM, "email": email})


def test_trial_creates_a_separate_empty_organisation_on_the_community_limits(
    client, db_session, demo_org
):
    from app.models import ArchiMateElement
    from app.models.subscription import SubscriptionPlan, SubscriptionStatus
    from app.models.user import User
    from app.services import billing_plans

    demo_organisation, _ = demo_org
    email, resp = _sign_up_trial(client)
    assert resp.status_code == 302
    user = User.query.filter_by(email=email).one()
    org = user.organization
    assert org.id != demo_organisation.id
    assert not demo.is_demonstration(org)

    sub = billing_plans.current_subscription(org)
    assert sub.status == SubscriptionStatus.trialing
    assert sub.plan == SubscriptionPlan.free
    assert org.settings["trial"]["started_from_demonstration"] is True
    status = billing_plans.user_limit_status(org.id)
    assert status["limit"] == 3 and status["used"] == 1

    # Nothing of the demonstration comes across.
    assert ArchiMateElement.query.filter_by(organization_id=org.id).count() == 0
    assert "demonstration" not in (org.settings or {})


def test_trial_respects_the_people_limit(client, db_session, demo_org):
    from app.models.user import Role, User
    from app.services import billing_plans

    email, _ = _sign_up_trial(client)
    org = User.query.filter_by(email=email).one().organization
    for i in range(2):
        db_session.add(
            User(
                email=f"m{i}-{uuid.uuid4().hex[:6]}@example.com",
                organization_id=org.id,
                confirmed=True,
                role=Role.query.filter_by(name="User").first(),
            )
        )
        db_session.flush()
    assert billing_plans.user_limit_status(org.id)["limit_reached"] is True


def test_starting_a_trial_from_the_demonstration_signs_the_visitor_out_of_it(
    client, db_session, demo_org
):
    from app.models.user import User

    client.post("/demonstration")
    email, resp = _sign_up_trial(client)
    assert resp.status_code == 302
    user = User.query.filter_by(email=email).one()
    assert user.organization_id != demo_org[0].id
    text = client.get("/dashboard/").get_data(as_text=True)
    assert "demonstration-banner" not in text


def test_two_trials_are_isolated_from_each_other(client, app, db_session, demo_org):
    from app.models.user import User

    email_a, _ = _sign_up_trial(client)
    from flask import g

    # db_session holds one app context open, so the first request's cached
    # identity would otherwise answer for the second client.
    g.pop("_login_user", None)
    g.pop("current_org_id", None)
    email_b, _ = _sign_up_trial(app.test_client())
    a = User.query.filter_by(email=email_a).one().organization_id
    b = User.query.filter_by(email=email_b).one().organization_id
    assert a != b


def test_the_demonstration_organisation_cannot_become_a_trial(db_session, demo_org):
    _, viewer = demo_org
    with pytest.raises(ValueError):
        demo.start_trial(viewer)


def test_trial_form_states_that_the_demonstration_does_not_come_along(client):
    text = client.get("/demonstration/trial").get_data(as_text=True)
    assert "Start your trial" in text
    assert "None of the demonstration" in text
