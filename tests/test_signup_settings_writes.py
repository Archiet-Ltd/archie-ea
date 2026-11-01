"""Settings are one JSON value written back whole. A sign-up step that writes its own section from a copy read
earlier must not erase what another request stored meanwhile, such as the profile a person just confirmed."""

import json

from app import db
from app.models.organization import Organization


def _store_elsewhere(org_id, key, value):
    """What a second request does: a committed write the first request's loaded copy has not seen."""
    row = db.session.execute(
        db.text("SELECT settings FROM organizations WHERE id = :i"), {"i": org_id}).scalar() or {}
    row = dict(row)
    row[key] = value
    db.session.execute(
        db.text("UPDATE organizations SET settings = CAST(:s AS json) WHERE id = :i"),
        {"s": json.dumps(row), "i": org_id})


def test_a_sign_up_write_from_an_older_copy_keeps_the_confirmed_profile(db_session, make_org):
    from app.modules.account.services import onboarding_service as onboarding

    mine, other = make_org("signup"), make_org("signup-other")
    mine.settings, other.settings = {"company_context": {"industry": "banking"}}, {}
    db_session.flush()
    assert mine.settings  # the copy this request holds, read before the profile was confirmed
    _store_elsewhere(mine.id, "company_profile", {"company_name": {"value": "Acme Robotics Group"}})

    onboarding.mark_profile_confirmed(mine)
    onboarding._remember_loaded(mine, "business_model")

    db.session.expire_all()
    kept = db.session.get(Organization, mine.id).settings
    assert kept["company_profile"]["company_name"]["value"] == "Acme Robotics Group"
    assert kept["company_context"]["canvases_loaded"] == ["business_model"]
    assert "profile_confirmed_at" in kept["onboarding"]
    assert "company_profile" not in (db.session.get(Organization, other.id).settings or {})


def test_two_interleaved_sign_up_writers_each_keep_the_others_key(db_session, make_org):
    """Both requests loaded the organisation before either wrote. Each then writes only its own section, from
    the stored copy, so the second writer does not undo the first."""
    from app.modules.account.services import onboarding_service as onboarding

    mine, other = make_org("interleave"), make_org("interleave-other")
    mine.settings = other.settings = {"company_context": {"industry": "banking"}}
    db_session.flush()
    assert mine.settings["company_context"] == {"industry": "banking"}  # the copy both requests hold

    # while both requests hold that copy, a third stores a canvas as loaded and a site, and commits
    _store_elsewhere(mine.id, "onboarding", {"website": {"url": "https://acme.example"}})
    _store_elsewhere(mine.id, "company_context", {"industry": "banking", "canvases_loaded": ["lean_canvas"]})

    onboarding.save_choices(mine, "retail", [], kind="startup")  # writes company_context
    onboarding.mark_finished(mine)  # writes onboarding

    db.session.expire_all()
    kept = db.session.get(Organization, mine.id).settings
    assert kept["onboarding"]["website"] == {"url": "https://acme.example"}
    assert "finished_at" in kept["onboarding"]
    assert kept["company_context"]["canvases_loaded"] == ["lean_canvas"]
    assert kept["company_context"]["industry"] == "retail" and kept["company_context"]["kind"] == "startup"
    assert db.session.get(Organization, other.id).settings == {"company_context": {"industry": "banking"}}


def test_saving_the_company_kind_keeps_the_confirmed_profile(client, login_as, db_session, make_org):
    import uuid

    from app.models.user import Role, User

    mine = make_org("kind")
    mine.settings = {}
    Role.insert_roles()
    user = User(email="kind.%s@example.test" % uuid.uuid4().hex[:8], first_name="Ki", last_name="Nd",
                organization_id=mine.id, enterprise_role="solution_architect", confirmed=True)
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "Sup3r-Secret!pass"
    db_session.add(user)
    db_session.flush()
    assert mine.settings == {}  # the copy the request holds
    _store_elsewhere(mine.id, "company_profile", {"company_name": {"value": "Acme Robotics Group"}})

    login_as(client, user)
    reply = client.post("/dashboard/api/company-kind", json={"kind": "enterprise"})
    assert reply.status_code == 200, reply.get_data(as_text=True)

    db.session.expire_all()
    kept = db.session.get(Organization, mine.id).settings
    assert kept["company_profile"]["company_name"]["value"] == "Acme Robotics Group"
    assert kept["company_context"]["kind"] == "enterprise"
