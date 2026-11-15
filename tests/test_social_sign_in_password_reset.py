"""A person who signs in only with Google or Microsoft can ask for a reset link and set a first password."""
import uuid
from datetime import timedelta

from tests.test_account_mail_flows import (  # noqa: F401  (fixtures)
    NEW_PASSWORD, _anonymous, _fresh, _link, _main, _make_org, _make_user, _request_reset, _tokens,
    mail_on, outbox,
)


def _social_only(db_session, org, confirmed=True):
    user = _make_user(db_session, org)
    user.confirmed = confirmed
    user.password_hash = None
    db_session.commit()
    return user


def test_a_confirmed_account_with_no_password_gets_a_link_that_sets_its_first_password_once(app, db_session, mail_on, outbox):
    org_a, org_b = _make_org(db_session, "SP1"), _make_org(db_session, "SP2")
    person = _social_only(db_session, org_a)
    other = _make_user(db_session, org_b)
    db_session.commit()
    other_hash = other.password_hash

    _request_reset(app, {"email": person.email})
    assert [m.recipients for m in outbox] == [[person.email]]
    path = _link(outbox[0], "/account/reset-password/")

    resp = _anonymous(app).post(path, data={"password": NEW_PASSWORD, "password2": NEW_PASSWORD})
    assert resp.status_code == 302
    assert _fresh(person).verify_password(NEW_PASSWORD)
    assert _fresh(other).password_hash == other_hash

    again = _anonymous(app).post(path, data={"password": "Another-Horse-99!", "password2": "Another-Horse-99!"})
    assert again.status_code == 410
    assert _fresh(person).verify_password(NEW_PASSWORD)


def test_the_link_for_a_no_password_account_expires_like_the_others(app, db_session, mail_on, outbox):
    from app.models.account_token import _utcnow

    person = _social_only(db_session, _make_org(db_session, "SP3"))
    _request_reset(app, {"email": person.email})
    path = _link(outbox[0], "/account/reset-password/")
    row = _tokens(person.id, "password_reset")[0]
    row.expires_at = _utcnow() - timedelta(seconds=1)
    db_session.commit()

    resp = _anonymous(app).post(path, data={"password": NEW_PASSWORD, "password2": NEW_PASSWORD})
    assert resp.status_code == 410
    assert _fresh(person).password_hash is None


def test_known_no_password_and_unknown_addresses_get_the_same_answer(app, db_session, mail_on, outbox):
    person = _social_only(db_session, _make_org(db_session, "SP4"))
    unknown = "nobody-{}@example.com".format(uuid.uuid4().hex[:8])

    known_resp = _request_reset(app, {"email": person.email})
    unknown_resp = _request_reset(app, {"email": unknown})

    assert known_resp.status_code == unknown_resp.status_code == 200
    assert _main(known_resp).replace(person.email, "ADDRESS") == _main(unknown_resp).replace(unknown, "ADDRESS")
    assert [m.recipients for m in outbox] == [[person.email]]


def test_an_unconfirmed_account_with_no_password_gets_no_link(app, db_session, mail_on, outbox):
    person = _social_only(db_session, _make_org(db_session, "SP5"), confirmed=False)
    _request_reset(app, {"email": person.email})
    assert outbox == []
    assert _tokens(person.id, "password_reset") == []
