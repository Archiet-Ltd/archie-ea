"""The composer page a cited answer opens answers 404 for another organisation's system."""

import uuid

from tests.test_team_invite_acceptance import _make_org, _make_user


def test_the_landing_for_another_organisations_system_is_not_found_and_names_nothing_of_it(
    app, db_session, login_as, client
):
    from app import db
    from app.models import ArchiMateElement

    tag = uuid.uuid4().hex[:8]
    mine, other = _make_org(db_session, "L1"), _make_org(db_session, "L2")
    me = _make_user(db_session, mine, email="landing-%s@example.com" % tag)
    secret = "Secret ledger %s" % tag
    theirs = ArchiMateElement(name=secret, type="ApplicationComponent", layer="application", organization_id=other.id)
    own = ArchiMateElement(name="Own system %s" % tag, type="ApplicationComponent", layer="application",
                           organization_id=mine.id)
    db.session.add_all([theirs, own])
    db_session.commit()

    login_as(client, me)
    refused = client.get("/archimate/composer?element=%d" % theirs.id)
    opened = client.get("/archimate/composer?element=%d" % own.id)

    assert refused.status_code == 404 and secret.encode() not in refused.data
    assert opened.status_code == 200 and b"Own system %s" % tag.encode() in opened.data
