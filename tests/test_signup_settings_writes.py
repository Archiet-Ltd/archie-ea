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
