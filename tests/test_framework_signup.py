"""A visitor who starts sign-up from a framework's page has that framework kept for the company step
(tests/test_framework_pages_journey.py clicks through and sees it ticked).

Two real organisations are created by two real sign-ups, so a choice made for one is shown to be kept
out of the other.
"""
import uuid

from tests.test_account_mail_flows import PASSWORD, _anonymous, mail_off  # noqa: F401


def _sign_up(app, link):
    email = "framework-{}@example.com".format(uuid.uuid4().hex[:8])
    client = _anonymous(app)
    client.get(link)
    response = client.post(link, data={
        "first_name": "Frame", "last_name": "Work", "email": email, "password": PASSWORD, "password2": PASSWORD,
    })
    assert response.status_code == 302
    return client, email


def _org_of(email):
    from app import db
    from app.models import Organization
    from app.models.user import User

    return db.session.get(Organization, User.find_by_email(email).organization_id)


def test_the_framework_on_the_sign_up_link_is_kept_for_the_company_step(app, db_session, mail_off):  # noqa: F811
    from app.modules.account.services import onboarding_service

    _, email = _sign_up(app, "/account/register?framework=iso27001")
    assert onboarding_service.choices(_org_of(email))["frameworks"] == ["iso27001"]


def test_another_company_signing_up_without_a_framework_has_none_ticked(app, db_session, mail_off):  # noqa: F811
    from app.modules.account.services import onboarding_service

    _, first = _sign_up(app, "/account/register?framework=gdpr")
    _, second = _sign_up(app, "/account/register")
    assert onboarding_service.choices(_org_of(first))["frameworks"] == ["gdpr"]
    assert onboarding_service.choices(_org_of(second))["frameworks"] == []


def test_a_name_that_is_not_a_sign_up_framework_is_ignored(app, db_session, mail_off):  # noqa: F811
    from app.modules.account.services import onboarding_service

    _, email = _sign_up(app, "/account/register?framework=not-a-framework")
    assert onboarding_service.choices(_org_of(email))["frameworks"] == []
