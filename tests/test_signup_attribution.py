"""Where each sign-up came from, and who is stuck.

A visitor arrives on /account/register with utm_source, utm_campaign and ref; the three values are kept
on the account they create. Platform admins read them, with the step each person reached, on the
Sign-ups list; the founder is mailed once when a new account has not reached a first answer in 30 minutes.
"""
import uuid
from datetime import datetime, timedelta

import pytest

from tests.test_account_mail_flows import PASSWORD, _anonymous, mail_off, mail_on, outbox  # noqa: F401

FOUNDER = "founder@example.com"
LINK = "/account/register?utm_source=outreach&utm_campaign=wave1&ref=w1-07"


def _post(client, email):
    return client.post("/account/register", data={
        "first_name": "Wave", "last_name": "One", "email": email, "password": PASSWORD, "password2": PASSWORD,
    })


def _sign_up(client, email, link=LINK):
    client.get(link)
    return _post(client, email)


def _email():
    return "wave-{}@example.com".format(uuid.uuid4().hex[:8])


def _user(email):
    from app.models.user import User

    return User.find_by_email(email)


@pytest.fixture
def founder(app, db_session, monkeypatch):
    """The founder's address is configured, and no sign-up from an earlier run on this database is in play
    (the platform-wide check and report read every account, so earlier ones would be counted too)."""
    from app.models.user import User

    monkeypatch.setitem(app.config, "FOUNDER_ALERT_EMAIL", FOUNDER)
    User.query.filter(User.signup_at.isnot(None)).update({"signup_at": None}, synchronize_session=False)  # tenant-scoping-ok: test set-up
    db_session.flush()


def test_the_three_values_on_the_register_link_are_kept_on_the_new_account(app, db_session, mail_off):  # noqa: F811
    email = _email()
    resp = _sign_up(_anonymous(app), email)
    assert resp.status_code == 302
    user = _user(email)
    assert (user.signup_source, user.signup_campaign, user.signup_ref) == ("outreach", "wave1", "w1-07")
    assert user.signup_at is not None


def test_the_values_survive_pages_between_the_link_and_the_form(app, db_session, mail_off):  # noqa: F811
    client = _anonymous(app)
    client.get(LINK)
    client.get("/pricing")  # the visitor looks around before signing up
    email = _email()
    _post(client, email)
    assert _user(email).signup_ref == "w1-07"


def test_the_first_values_seen_win_and_hostile_values_are_cleaned_and_limited(app, db_session, mail_off):  # noqa: F811
    client = _anonymous(app)
    client.get("/account/register?utm_source=%3Cscript%3Ealert(1)%3C/script%3E&ref=" + "x" * 300)
    client.get("/account/register?utm_source=other&utm_campaign=later&ref=w9-99")
    email = _email()
    _post(client, email)
    user = _user(email)
    assert "<" not in user.signup_source and ">" not in user.signup_source
    assert user.signup_source.startswith("scriptalert")
    assert len(user.signup_ref) == 64 and set(user.signup_ref) == {"x"}
    assert user.signup_campaign is None


def test_a_sign_up_with_no_link_values_still_records_when_it_happened(app, db_session, mail_off):  # noqa: F811
    email = _email()
    _post(_anonymous(app), email)
    user = _user(email)
    assert user.signup_source is None and user.signup_ref is None
    assert user.signup_at is not None


# -- the staff-only list ----------------------------------------------------------------------------


def _platform_admin(db_session):
    from tests.test_team_invite_acceptance import _make_org, _make_user

    admin = _make_user(db_session, _make_org(db_session, "sgnadm"), org_admin=True)
    admin.is_platform_admin = True
    db_session.flush()
    return admin


def test_the_signups_list_shows_source_campaign_ref_and_step_to_platform_admins_only(
        app, db_session, login_as, mail_off):  # noqa: F811
    email = _email()
    _sign_up(_anonymous(app), email, "/account/register?utm_source=outreach&utm_campaign=wave1&ref=%3Cb%3Ew1-07")
    admin = _platform_admin(db_session)

    client = _anonymous(app)
    login_as(client, admin)
    page = client.get("/admin/signups")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    row = html[html.index(email) - 400: html.index(email) + 1200]
    for shown in ("outreach", "wave1", "w1-07", "Address confirmed"):
        assert shown in row, shown
    assert "<b>w1-07" not in html  # the visitor's text is shown as text

    from tests.test_team_invite_acceptance import _make_org, _make_user

    other = _make_user(db_session, _make_org(db_session, "sgnoth"), org_admin=True)
    client = _anonymous(app)
    login_as(client, other)
    assert client.get("/admin/signups").status_code in (302, 401, 403)
    anonymous = _anonymous(app).get("/admin/signups")
    assert anonymous.status_code in (302, 401, 403)
    assert email not in anonymous.get_data(as_text=True)


def test_the_step_moves_on_as_the_person_does_and_first_answer_time_is_measured(app, db_session, mail_off):  # noqa: F811
    from app.modules.account.services import onboarding_service as onboarding
    from app.services import signup_attribution as origin

    email = _email()
    _sign_up(_anonymous(app), email)
    user = _user(email)
    assert origin.step_of(user)[0] == "address_confirmed"  # no mail server: usable at once
    user.confirmed = False
    assert origin.step_of(user)[0] == "signed_up"
    user.confirmed = True
    onboarding.mark_profile_confirmed(user.organization)
    assert origin.step_of(user)[0] == "profile_confirmed"
    _answer(user, minutes_after=12)
    assert origin.step_of(user)[0] == "first_answer"
    assert origin.minutes_to_first_answer(user) == pytest.approx(12, abs=1)


def _answer(user, minutes_after):
    from app import db
    from app.models.conversation import ConversationMessageRecord, ConversationThreadRecord

    when = user.signup_at + timedelta(minutes=minutes_after)
    thread = ConversationThreadRecord(
        id=str(uuid.uuid4()), user_id=user.id, title="What breaks?", model="m", created_at=when,
        updated_at=when, message_count=2, organization_id=user.organization_id)
    db.session.add(thread)
    db.session.flush()
    db.session.add(ConversationMessageRecord(
        id=str(uuid.uuid4()), thread_id=thread.id, role="assistant", content="It breaks checkout.", created_at=when))
    db.session.flush()


# -- the 30-minute check ----------------------------------------------------------------------------


def _aged(app, minutes):
    from app import db

    email = _email()
    _sign_up(_anonymous(app), email)
    user = _user(email)
    user.confirmed = True  # the address is confirmed; the profile is not
    user.signup_at = datetime.utcnow() - timedelta(minutes=minutes)
    db.session.flush()
    return user


def _founder_mail(outbox):  # noqa: F811
    return [m for m in outbox if FOUNDER in m.recipients]


def test_an_account_stuck_at_the_confirm_step_raises_one_alert_and_a_second_run_sends_none(
        app, db_session, mail_on, outbox, founder):  # noqa: F811
    from app.services import signup_attribution as origin

    stuck = _aged(app, 45)
    assert origin.alert_stuck_accounts(app) == [stuck.id]
    alerts = _founder_mail(outbox)
    assert len(alerts) == 1
    text = alerts[0].body
    assert stuck.email in text and "w1-07" in text and "Address confirmed" in text
    assert "confirm" in text.lower()
    assert origin.alert_stuck_accounts(app) == []
    assert len(_founder_mail(outbox)) == 1


def test_a_completed_a_recent_and_a_long_ago_account_raise_no_alert(
        app, db_session, mail_on, outbox, founder):  # noqa: F811
    from app.services import signup_attribution as origin

    done = _aged(app, 90)
    _answer(done, minutes_after=10)
    _aged(app, 10)  # not 30 minutes yet
    ancient = _aged(app, 60 * 24 * 10)  # signed up ten days ago: not news
    assert origin.alert_stuck_accounts(app) == []
    assert _founder_mail(outbox) == []
    assert ancient.signup_alerted_at is None


def test_nothing_is_sent_or_marked_without_a_configured_founder_address(
        app, db_session, mail_on, outbox, founder, monkeypatch):  # noqa: F811
    from app.services import signup_attribution as origin

    monkeypatch.setitem(app.config, "FOUNDER_ALERT_EMAIL", None)
    stuck = _aged(app, 45)
    assert origin.alert_stuck_accounts(app) == []
    assert _founder_mail(outbox) == [] and stuck.signup_alerted_at is None


def test_the_daily_report_lists_the_last_day_of_sign_ups_with_source_and_step(
        app, db_session, mail_on, outbox, founder):  # noqa: F811
    from app.services import signup_attribution as origin

    user = _aged(app, 120)
    assert origin.send_daily_report(app) is True
    report = _founder_mail(outbox)
    assert len(report) == 1
    assert user.email in report[0].body and "w1-07" in report[0].body and "wave1" in report[0].body
