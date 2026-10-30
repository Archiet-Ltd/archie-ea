"""A closed route answers the same for two organisations, so it cannot reveal whether a record exists.

At wave 1, signed in as organisation A and then as organisation B, every route the wave closes answers the
same way whether the record in the address belongs to A, belongs to B, or does not exist. A page answers the
early-access page, a write or data call answers the same plain 404; nothing says "found" or "forbidden".
"""

from __future__ import annotations

import re
import uuid

import pytest

from app.release_waves import CLOSED, configured_wave, entry_for, state_for

SAMPLE = 120  # closed rules checked, spread evenly over the whole map


@pytest.fixture
def wave1(app, _schema, monkeypatch):
    monkeypatch.setitem(app.config, "RELEASE_WAVE", "1")
    monkeypatch.delenv("RELEASE_WAVE", raising=False)
    return app


def _user(db_session, org, label):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()
    user = User(email=f"{label}-{uuid.uuid4().hex[:8]}@example.com", first_name=label, last_name="User",
                organization_id=org.id, role=role, is_org_admin=True, confirmed=True)
    user.password = "pw-original"
    db_session.add(user)
    db_session.flush()
    return user


def _address(rule, record_id):
    path = rule.rule
    for name, converter in rule._converters.items():
        kind = type(converter).__name__
        if kind == "IntegerConverter":
            value = str(record_id)
        elif kind == "AnyConverter":
            # "x" is not one of the converter's own literals (e.g. the ArchiMate
            # layer segment), so it never matches this rule at all and the request
            # falls through to the app's ordinary signed-in 404 instead of this
            # rule's own closed-route answer. Use one of the converter's real
            # values so the probe actually exercises the rule being checked.
            value = sorted(converter.items)[0]
        else:
            value = "x"
        path = re.sub(r"<[^>]*\b%s>" % re.escape(name), value, path)
    return path


def _normal(reply):
    body = reply.get_data(as_text=True)
    body = re.sub(r'nonce="[^"]*"', 'nonce=""', body)
    body = re.sub(r'(csrf[^"]*"\s+(?:content|value)=")[^"]*', r"\1", body)
    body = re.sub(r'value="[A-Za-z0-9._-]{20,}"', 'value=""', body)
    # the signed-in person's own name in the page header differs between two people by design
    body = re.sub(r"[\w.+-]+@example\.com", "EMAIL", body)
    body = re.sub(r'(font-medium text-foreground">)[a-zA-Z](</span>)', r"\1X\2", body)
    body = re.sub(r'content="[A-Za-z0-9._-]{20,}"', 'content=""', body)
    return reply.status_code, body


def test_a_closed_route_answers_the_same_to_both_organisations_whatever_the_record(wave1, db_session, make_org, login_as):
    from app.models import ArchiMateElement

    org_a, org_b = make_org("closeda"), make_org("closedb")
    user_a, user_b = _user(db_session, org_a, "alpha"), _user(db_session, org_b, "bravo")
    mine = ArchiMateElement(name="Alpha ledger", type="ApplicationComponent", layer="application",
                            organization_id=org_a.id)
    db_session.add(mine)
    db_session.flush()
    wave = configured_wave()
    assert wave == 1

    closed = []
    for rule in sorted(wave1.url_map.iter_rules(), key=lambda r: r.rule):
        if state_for(entry_for(rule.rule, rule.endpoint), wave) != CLOSED:
            continue
        methods = sorted(rule.methods - {"HEAD", "OPTIONS"})
        if methods and not rule.rule.startswith("/static"):
            closed.append((rule, methods[0]))
    assert len(closed) > 500, len(closed)
    step = max(1, len(closed) // SAMPLE)
    picked = closed[::step]
    # a rule that names a record: always include a few
    picked += [c for c in closed if c[0]._converters][:30]

    answers = {}
    for who, user in (("a", user_a), ("b", user_b)):
        client = wave1.test_client()
        login_as(client, user)
        for rule, method in picked:
            for label, record in (("mine", mine.id), ("none", 999999999)):
                reply = client.open(_address(rule, record), method=method, follow_redirects=False)
                answers[(who, rule.rule, method, label)] = _normal(reply)

    different = []
    for rule, method in picked:
        seen = {answers[(who, rule.rule, method, label)] for who in "ab" for label in ("mine", "none")}
        if len(seen) > 1:
            different.append((method, rule.rule, sorted({a[0] for a in seen})))
    assert not different, f"{len(different)} closed routes answer differently by organisation or record: {different[:8]}"
    for key, (status, body) in answers.items():
        assert status in (200, 302, 404), (key, status)
        if key[2] != "GET":
            assert status == 404, key
