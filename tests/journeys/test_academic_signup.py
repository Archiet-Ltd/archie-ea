"""A lecturer signs up with a university address and their organisation is on Education.

The goal: someone at a listed university or college signs up, confirms the address, says whether they
teach or study, accepts the terms, and their organisation is free for everyone they bring in until the
next 31 August. An address the list does not match is never refused and carries on with Community.
"""
import html
import re
import time
import uuid
from datetime import date, datetime, timedelta

import pytest
from flask.testing import FlaskClient

PASSWORD = "Correct-horse-9"
SENDER = "no-reply@example.com"
PERSON_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"


@pytest.fixture(scope="session")
def app(request, worker_database):
    """The shared session application: this journey uses the shared transactional fixtures, which the
    module-scoped application in this folder's conftest cannot serve."""
    from app import create_app

    application = create_app("testing")
    application.config["TESTING"] = True
    application.config["WTF_CSRF_ENABLED"] = False
    return application


@pytest.fixture
def outbox(app):
    from app.extensions import mail

    with mail.record_messages() as messages:
        yield messages


@pytest.fixture
def mail_on(app, monkeypatch):
    monkeypatch.setitem(app.config, "MAIL_DEFAULT_SENDER", SENDER)
    monkeypatch.setitem(app.config, "SIGNUP_FORM_TOKEN_REQUIRED", True)
    monkeypatch.setitem(app.config, "SIGNUP_MIN_SECONDS", 1)
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", False)


def _forget_cached_person():
    """One app context is held open for the whole test, so the signed-in person and organisation that a
    request caches on ``g`` would otherwise outlive the request and speak for the next one."""
    from flask import g, has_app_context

    if has_app_context():
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)


class _Person(FlaskClient):
    def open(self, *args, **kwargs):
        _forget_cached_person()
        return super().open(*args, **kwargs)


def _client(app):
    client = app.test_client()
    client.__class__ = _Person
    return client


def _text(response):
    body = re.sub(r"<(script|style)\b.*?</\1>", " ", response.get_data(as_text=True), flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body)))


def _sign_up(client, email, first="Lena"):
    page = client.get("/account/register", headers={"User-Agent": PERSON_AGENT}).get_data(as_text=True)
    found = re.search(r'name="form_issued"[^>]*value="([^"]+)"', page) or re.search(
        r'value="([^"]+)"[^>]*name="form_issued"', page
    )
    time.sleep(1.2)
    return client.post(
        "/account/register",
        data={"first_name": first, "last_name": "Marti", "email": email, "password": PASSWORD,
              "password2": PASSWORD, "form_issued": found.group(1), "nickname": ""},
        headers={"User-Agent": PERSON_AGENT},
    )


def _confirm(client, outbox, email):
    message = [m for m in outbox if email in m.recipients and "/account/confirm-account/" in m.body][-1]
    path = re.search(r"https?://[^\s\"'<>]+(/account/confirm-account/[^\s\"'<>]+)", message.body).group(1)
    return client.get(path)


def _address(prefix, domain):
    return "{}.{}@{}".format(prefix, uuid.uuid4().hex[:8], domain)


def _expected_end(today):
    """The next 31 August, or the one a year later when fewer than 60 days remain."""
    end = date(today.year, 8, 31)
    if end < today or (end - today).days < 60:
        end = date(today.year + 1, 8, 31)
    return end


def _signed_up_and_confirmed(app, outbox, domain, prefix="l.marti"):
    email = _address(prefix, domain)
    client = _client(app)
    assert _sign_up(client, email).status_code == 302
    return client, email, _confirm(client, outbox, email)


def _subscription_of(email):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.billing_plans import current_subscription

    db.session.expire_all()
    user = User.find_by_email(email)
    return current_subscription(Organization.query.get(user.organization_id))


def _invite_and_join(app, outbox, admin_client, admin_email, email):
    """The administrator invites an address; the invited person opens the link and sets a password."""
    from app import db
    from app.models.user import User

    from app.modules.account.services import invitation_service

    admin = User.find_by_email(admin_email)
    with app.test_request_context("/"):
        invitation_service.invite_new_person(admin.organization_id, admin, email, org_role="viewer")
    sent = [m for m in outbox if email in m.recipients]
    assert sent
    message = sent[-1]
    path = re.search(r"https?://[^\s\"'<>]+(/account/join/[^\s\"'<>]+)", message.body).group(1)
    guest = _client(app)
    joined = guest.post(path, data={"password": PASSWORD, "password2": PASSWORD})
    assert joined.status_code == 302, _text(joined)[:300]
    db.session.expire_all()
    assert User.find_by_email(email).confirmed


def test_a_lecturer_at_a_university_is_on_education_until_the_next_31_august(app, db_session, mail_on, outbox):
    from app import db
    from app.models.subscription import SubscriptionPlan
    from app.modules.account.services import invitation_service
    from app.services.billing_plans import PlanLimitReached

    # A second organisation that signs up beforehand and must not be moved by the first one's grant.
    other, other_email, other_confirmed = _signed_up_and_confirmed(app, outbox, "gmail.com", prefix="other")
    assert "/education" not in other_confirmed.headers["Location"]

    # 1-2. The lecturer opens the confirmation link and lands on the eligibility page for their university.
    lecturer, email, confirmed = _signed_up_and_confirmed(app, outbox, "cs.uda.ad")
    assert confirmed.status_code == 302
    assert confirmed.headers["Location"].endswith("/education/eligibility")
    page = lecturer.get("/education/eligibility")
    words = _text(page)
    assert page.status_code == 200
    assert "You're at University of Andorra" in words
    assert "Andorra" in words and email in words
    assert "I teach" in words and "I study" in words
    assert "not to run my institution's own operations" in words
    assert "Start using Education" in words and "Carry on with Community" in words
    assert _subscription_of(email).plan != SubscriptionPlan.education  # nothing is granted by looking

    # The choice and the terms are both required.
    assert "Say whether you teach or study" in _text(lecturer.post("/education/eligibility", data={"terms": "yes"}))
    assert "Accept the terms" in _text(lecturer.post("/education/eligibility", data={"role": "teach"}))
    assert _subscription_of(email).plan != SubscriptionPlan.education
    accepted = lecturer.post("/education/eligibility", data={"role": "teach", "terms": "yes"})
    assert accepted.status_code == 302 and accepted.headers["Location"].endswith("/admin/billing/")

    # 3. The billing page says Education, free until the next 31 August (read from the clock).
    until = _expected_end(datetime.utcnow().date())
    billing = _text(lecturer.get("/admin/billing/"))
    assert "Education, free until 31 Aug {}".format(until.year) in billing
    assert "Free until" in billing and "31 Aug {}".format(until.year) in billing
    sub = _subscription_of(email)
    assert sub.plan == SubscriptionPlan.education and sub.offer == "education"
    assert sub.current_period_end.date() == until
    assert sub.offer_terms["role"] == "teach"
    assert sub.offer_terms["institution"] == "University of Andorra"
    assert sub.offer_terms["how"] == "list"
    assert sub.offer_terms["address"] == email
    assert sub.offer_terms["terms_accepted_at"]
    assert "Education" not in re.findall(r'data-testid="plan-card-(\w+)"', lecturer.get("/admin/billing/").get_data(as_text=True))

    # Opening the page again says so rather than offering it twice.
    assert "Your organisation is on Education" in _text(lecturer.get("/education/eligibility"))

    # 4. Four people are invited and all four join: five people, past the three Community allows.
    for number in range(4):
        _invite_and_join(app, outbox, lecturer, email, _address("student{}".format(number), "uda.ad"))
    assert "5 of unlimited people" in _text(lecturer.get("/admin/billing/"))

    # The other organisation is unchanged by the first one's grant.
    other_sub = _subscription_of(other_email)
    assert other_sub.plan != SubscriptionPlan.education and other_sub.offer is None
    other_billing = _text(other.get("/admin/billing/"))
    assert "Community" in other_billing and "Education, free until" not in other_billing

    # 7. The period ends: Community again, everyone still signs in, and a fifth invitation is refused.
    sub = _subscription_of(email)
    sub.current_period_end = datetime.utcnow() - timedelta(days=1)
    db.session.commit()
    ended = _text(lecturer.get("/admin/billing/"))
    assert "Community" in ended
    assert "Your free Education period ended on" in ended
    assert "Education, free until" not in ended
    from app.models.user import User

    admin = User.find_by_email(email)
    with pytest.raises(PlanLimitReached):
        invitation_service.invite_new_person(admin.organization_id, admin, _address("fifth", "uda.ad"), org_role="viewer")


def test_everyone_in_an_ended_education_organisation_still_signs_in(app, db_session, mail_on, outbox):
    from app import db

    lecturer, email, _ = _signed_up_and_confirmed(app, outbox, "cs.uda.ad")
    lecturer.post("/education/eligibility", data={"role": "study", "terms": "yes"})
    member = _address("student", "uda.ad")
    _invite_and_join(app, outbox, lecturer, email, member)
    sub = _subscription_of(email)
    sub.current_period_end = datetime.utcnow() - timedelta(days=1)
    db.session.commit()
    signed_in = _client(app).post("/account/login", data={"email": member, "password": PASSWORD})
    assert signed_in.status_code == 302 and "/account/login" not in signed_in.headers["Location"]


def test_an_alumni_address_reaches_community_and_is_never_refused(app, db_session, mail_on, outbox):
    from app.models.subscription import SubscriptionPlan

    client, email, confirmed = _signed_up_and_confirmed(app, outbox, "alumni.uda.ad", prefix="old.student")
    assert confirmed.status_code == 302 and "/education" not in confirmed.headers["Location"]
    page = client.get("/education/eligibility")
    words = _text(page)
    assert page.status_code == 200
    assert "We don't recognise this address as a university or college" in words
    assert "Carry on with Community" in words
    assert "Start using Education" not in words and "Check my institution" not in words
    assert 'href="/dashboard' in page.get_data(as_text=True) or "Carry on with Community" in words
    assert client.post("/education/eligibility", data={"role": "teach", "terms": "yes"}).status_code == 200
    assert _subscription_of(email).plan != SubscriptionPlan.education


def test_a_public_mailbox_is_never_sent_to_the_eligibility_page(app, db_session, mail_on, outbox):
    client, _email, confirmed = _signed_up_and_confirmed(app, outbox, "gmail.com", prefix="someone")
    assert "/education" not in confirmed.headers["Location"]
    assert "Start using Education" not in _text(client.get("/education/eligibility"))


def test_a_server_without_mail_never_grants_the_offer(app, db_session, monkeypatch):
    """Without mail an account is confirmed with no link, which proves nothing about the mailbox."""
    from app.models.subscription import SubscriptionPlan

    monkeypatch.setitem(app.config, "MAIL_DEFAULT_SENDER", None)
    monkeypatch.setitem(app.config, "MAIL_USERNAME", None)
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setitem(app.config, "SIGNUP_FORM_TOKEN_REQUIRED", True)
    monkeypatch.setitem(app.config, "SIGNUP_MIN_SECONDS", 1)
    email = _address("l.marti", "cs.uda.ad")
    client = _client(app)
    assert _sign_up(client, email).status_code == 302
    page = client.get("/education/eligibility")
    assert "Start using Education" not in _text(page)
    assert client.post("/education/eligibility", data={"role": "teach", "terms": "yes"}).status_code != 302
    assert _subscription_of(email).plan != SubscriptionPlan.education
