"""The Review screen and its proposal store (onboarding-redesign-v3 §6).

Covers the producer built from Screen 2's answers, the store's merge/decide
behaviour, the routes, and cross-organisation isolation.
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org):
    from app.models.user import User

    user = User(
        email=f"review-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Re",
        last_name="View",
        confirmed=True,
        organization_id=org.id,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_default_role(db_session):
    """Full-page renders need current_user.role set (the deprecated nav
    macro dereferences current_user.role.index) -- create the one default
    role User.__init__ looks for, same shape as Role.insert_roles()'s
    "Architect" row."""
    from app.models.user import Permission, Role

    role = Role(name=f"Architect-{uuid.uuid4().hex[:6]}", index="main", default=True, permissions=Permission.GENERAL)
    db_session.add(role)
    db_session.flush()
    return role


def _logged_in(db_session, make_org, client, login_as, name, *, with_role=False):
    if with_role:
        _make_default_role(db_session)
    org = make_org(name)
    user = _make_user(db_session, org)
    login_as(client, user)
    return org, user


# --- producers.from_answers -------------------------------------------------

def test_from_answers_produces_nothing_without_a_stage():
    from app.modules.onboarding.services import producers

    assert producers.from_answers({}) == []


def test_from_answers_derives_funding_stage_region_and_compliance():
    from app.modules.onboarding.services import producers

    candidates = producers.from_answers({
        "stage": "early_revenue",
        "region_europe_or_eu_customers": True,
        "handles_card_data_directly": False,
    })
    by_field = {c["field"]: c for c in candidates}

    assert by_field["funding_stage"]["value"] == "Seed"
    assert by_field["funding_stage"]["confidence"] == "likely"
    assert by_field["funding_stage"]["source_label"] == "From your answers"

    assert by_field["region"]["value"] == "Serves customers in Europe / the EU"
    assert by_field["region"]["confidence"] == "certain"

    assert by_field["compliance_hints"]["value"] == "GDPR / data-protection control"
    # Card data was not flagged, so payment_security must not appear.
    assert all(c["value"] != "Payment-security control" for c in candidates)


def test_from_answers_proposes_no_compliance_hint_without_a_trigger():
    """Honest limits: pre-revenue with neither flag set has no applicable
    controls in the stage baseline -- the producer must not invent one."""
    from app.modules.onboarding.services import producers

    candidates = producers.from_answers({"stage": "pre_revenue"})
    assert all(c["field"] != "compliance_hints" for c in candidates)
    assert any(c["field"] == "funding_stage" for c in candidates)


# --- proposals store ---------------------------------------------------------

def test_sync_creates_pending_proposals(app, db_session, make_org):
    from app.modules.onboarding.services import proposals

    org = make_org("sync")
    candidates = [{
        "field": "funding_stage", "value": "Seed", "source": "answers",
        "source_label": "From your answers", "confidence": "likely", "reason": "why",
    }]
    items = proposals.sync(org, candidates)

    assert len(items) == 1
    assert items[0]["status"] == "pending"
    assert items[0]["field"] == "funding_stage"
    assert items[0]["id"] == "funding_stage:seed"


def test_sync_does_not_reopen_a_decided_proposal(app, db_session, make_org):
    from app.modules.onboarding.services import proposals

    org = make_org("resync")
    candidate = {
        "field": "region", "value": "Serves customers in Europe / the EU",
        "source": "answers", "source_label": "From your answers",
        "confidence": "certain", "reason": "why",
    }
    proposals.sync(org, [candidate])
    pid = proposals.proposal_id("region", candidate["value"])
    proposals.decide(org, pid, "dismiss")

    items = proposals.sync(org, [candidate])
    assert items[0]["status"] == "dismissed", "a producer re-running must not undo a decision"


def test_sync_drops_a_stale_pending_proposal_whose_signal_disappeared(app, db_session, make_org):
    from app.modules.onboarding.services import proposals

    org = make_org("stale")
    candidate = {
        "field": "region", "value": "Serves customers in Europe / the EU",
        "source": "answers", "source_label": "From your answers",
        "confidence": "certain", "reason": "why",
    }
    proposals.sync(org, [candidate])
    items = proposals.sync(org, [])  # signal gone (e.g. the flag was unticked)

    assert items == []


def test_confirm_writes_the_value_to_the_profile(app, db_session, make_org):
    from app.modules.onboarding.services import profile, proposals

    org = make_org("confirm")
    candidate = {
        "field": "funding_stage", "value": "Seed", "source": "answers",
        "source_label": "From your answers", "confidence": "likely", "reason": "why",
    }
    proposals.sync(org, [candidate])
    pid = proposals.proposal_id("funding_stage", "Seed")

    result = proposals.decide(org, pid, "confirm", user_id=1)

    assert result["status"] == "confirmed"
    assert profile.read(org)["funding_stage"] == "Seed"


def test_edit_then_confirm_writes_the_edited_value(app, db_session, make_org):
    from app.modules.onboarding.services import profile, proposals

    org = make_org("edit")
    candidate = {
        "field": "funding_stage", "value": "Seed", "source": "answers",
        "source_label": "From your answers", "confidence": "likely", "reason": "why",
    }
    proposals.sync(org, [candidate])
    pid = proposals.proposal_id("funding_stage", "Seed")

    result = proposals.decide(org, pid, "edit", value="Seed, closing in Q2", user_id=1)

    assert result["status"] == "edited"
    assert profile.read(org)["funding_stage"] == "Seed, closing in Q2"


def test_dismiss_writes_nothing_to_the_profile(app, db_session, make_org):
    from app.modules.onboarding.services import profile, proposals

    org = make_org("dismiss")
    candidate = {
        "field": "funding_stage", "value": "Seed", "source": "answers",
        "source_label": "From your answers", "confidence": "likely", "reason": "why",
    }
    proposals.sync(org, [candidate])
    pid = proposals.proposal_id("funding_stage", "Seed")

    result = proposals.decide(org, pid, "dismiss", user_id=1)

    assert result["status"] == "dismissed"
    assert "funding_stage" not in profile.read(org)


def test_dismiss_never_reappears_as_pending(app, db_session, make_org):
    from app.modules.onboarding.services import proposals

    org = make_org("dismiss-memory")
    candidate = {
        "field": "region", "value": "Serves customers in Europe / the EU",
        "source": "answers", "source_label": "From your answers",
        "confidence": "certain", "reason": "why",
    }
    proposals.sync(org, [candidate])
    pid = proposals.proposal_id("region", candidate["value"])
    proposals.decide(org, pid, "dismiss")

    items = proposals.sync(org, [candidate])
    pending = [p for p in items if p["status"] == "pending"]
    assert pending == []


def test_multi_value_field_accumulates_without_duplicates(app, db_session, make_org):
    from app.modules.onboarding.services import profile, proposals

    org = make_org("multi")
    gdpr = {
        "field": "compliance_hints", "value": "GDPR / data-protection control",
        "source": "answers", "source_label": "From your answers",
        "confidence": "likely", "reason": "why",
    }
    payment = {
        "field": "compliance_hints", "value": "Payment-security control",
        "source": "answers", "source_label": "From your answers",
        "confidence": "likely", "reason": "why",
    }
    proposals.sync(org, [gdpr, payment])
    proposals.decide(org, proposals.proposal_id("compliance_hints", gdpr["value"]), "confirm")
    proposals.decide(org, proposals.proposal_id("compliance_hints", payment["value"]), "confirm")

    hints = profile.read(org)["compliance_hints"]
    assert set(hints) == {"GDPR / data-protection control", "Payment-security control"}
    assert len(hints) == 2  # no duplicate entries


def test_decide_unknown_id_raises_keyerror(app, db_session, make_org):
    from app.modules.onboarding.services import proposals

    org = make_org("unknown")
    with pytest.raises(KeyError):
        proposals.decide(org, "nope:nope", "confirm")


def test_decide_empty_edit_value_raises_valueerror(app, db_session, make_org):
    from app.modules.onboarding.services import proposals

    org = make_org("empty-edit")
    candidate = {
        "field": "funding_stage", "value": "Seed", "source": "answers",
        "source_label": "From your answers", "confidence": "likely", "reason": "why",
    }
    proposals.sync(org, [candidate])
    pid = proposals.proposal_id("funding_stage", "Seed")

    with pytest.raises(ValueError):
        proposals.decide(org, pid, "edit", value="   ")


# --- tenant isolation ---------------------------------------------------------

def test_proposals_are_isolated_between_organizations(app, db_session, make_org):
    from app.modules.onboarding.services import producers, profile, proposals

    org_a = make_org("iso-a")
    org_b = make_org("iso-b")
    profile.write(org_a, stage="early_revenue", region_europe_or_eu_customers=True)
    profile.write(org_b, stage="early_revenue", region_europe_or_eu_customers=True)

    # Same signal in both orgs -> same candidate ids, but each org's store is
    # its own Organization row, so deciding in one must never touch the other.
    items_a = proposals.sync(org_a, producers.from_answers(profile.read(org_a)))
    items_b = proposals.sync(org_b, producers.from_answers(profile.read(org_b)))
    assert {p["id"] for p in items_a} == {p["id"] for p in items_b}, "fixture sanity: identical signals"

    funding_id = proposals.proposal_id("funding_stage", "Seed")
    proposals.decide(org_a, funding_id, "confirm")
    proposals.decide(org_b, funding_id, "dismiss")

    a_after = proposals.read(org_a)
    b_after = proposals.read(org_b)
    a_funding = next(p for p in a_after if p["id"] == funding_id)
    b_funding = next(p for p in b_after if p["id"] == funding_id)

    assert a_funding["status"] == "confirmed"
    assert b_funding["status"] == "dismissed", "org B's decision must not have been overwritten by org A's"
    assert profile.read(org_a)["funding_stage"] == "Seed"
    assert "funding_stage" not in profile.read(org_b), "org B dismissed -- nothing should be written to its profile"


def test_review_route_isolation_between_organizations(app, db_session, make_org, client, login_as):
    from app.modules.onboarding.services import profile, proposals

    org_a, user_a = _logged_in(db_session, make_org, client, login_as, "route-iso-a", with_role=True)
    org_b, user_b = _logged_in(db_session, make_org, client, login_as, "route-iso-b", with_role=True)
    profile.write(org_a, stage="growing")
    profile.write(org_b, stage="growing")

    login_as(client, user_a)
    resp = client.get("/onboarding/review")
    assert resp.status_code == 200
    from app.modules.onboarding.services import producers
    candidate_id = proposals.proposal_id("funding_stage", "Series A or B")
    resp = client.post(f"/onboarding/review/{candidate_id}/action", json={"action": "confirm"})
    assert resp.status_code == 200, resp.get_data(as_text=True)

    db_session.refresh(org_a)
    db_session.refresh(org_b)
    assert profile.read(org_a)["funding_stage"] == "Series A or B"
    assert "funding_stage" not in profile.read(org_b), "confirming in org A's request must never write org B's profile"

    login_as(client, user_b)
    resp = client.get("/onboarding/review")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Confirmed: Series A or B" not in html, "org B must see its own pending proposal, not org A's decision"


# --- routes: full render ------------------------------------------------------

def test_review_page_shows_the_honest_empty_state(app, db_session, make_org, client, login_as):
    org, _ = _logged_in(db_session, make_org, client, login_as, "empty", with_role=True)

    resp = client.get("/onboarding/review")

    assert resp.status_code == 200, resp.get_data(as_text=True)
    html = resp.get_data(as_text=True)
    assert "When we read your website, what we find appears here for you to confirm." in html


def test_review_page_lists_proposals_with_source_and_confidence(app, db_session, make_org, client, login_as):
    from app.modules.onboarding.services import profile

    org, _ = _logged_in(db_session, make_org, client, login_as, "listed", with_role=True)
    profile.write(org, stage="early_revenue", region_europe_or_eu_customers=True)

    resp = client.get("/onboarding/review")

    assert resp.status_code == 200, resp.get_data(as_text=True)
    html = resp.get_data(as_text=True)
    assert "From your answers" in html
    assert "likely" in html
    assert "certain" in html
    assert "Seed" in html
    assert "Serves customers in Europe" in html


def test_review_confirm_edit_dismiss_round_trip_through_the_route(
    app, db_session, make_org, client, login_as
):
    from app.modules.onboarding.services import profile, proposals

    org, _ = _logged_in(db_session, make_org, client, login_as, "roundtrip", with_role=True)
    profile.write(org, stage="growing")

    pid = proposals.proposal_id("funding_stage", "Series A or B")
    client.get("/onboarding/review")  # seeds the store, as visiting the page would

    resp = client.post(f"/onboarding/review/{pid}/action", json={"action": "confirm"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    db_session.refresh(org)
    assert profile.read(org)["funding_stage"] == "Series A or B"


def test_review_edit_route_writes_edited_value(app, db_session, make_org, client, login_as):
    from app.modules.onboarding.services import profile, proposals

    org, _ = _logged_in(db_session, make_org, client, login_as, "editroute", with_role=True)
    profile.write(org, stage="growing")
    pid = proposals.proposal_id("funding_stage", "Series A or B")
    client.get("/onboarding/review")  # seeds the store, as visiting the page would

    resp = client.post(
        f"/onboarding/review/{pid}/action",
        json={"action": "edit", "value": "Series B, term sheet signed"},
    )

    assert resp.status_code == 200, resp.get_data(as_text=True)
    db_session.refresh(org)
    assert profile.read(org)["funding_stage"] == "Series B, term sheet signed"


def test_review_dismiss_route_writes_nothing(app, db_session, make_org, client, login_as):
    from app.modules.onboarding.services import profile, proposals

    org, _ = _logged_in(db_session, make_org, client, login_as, "dismissroute", with_role=True)
    profile.write(org, stage="growing")
    pid = proposals.proposal_id("funding_stage", "Series A or B")
    client.get("/onboarding/review")  # seeds the store, as visiting the page would

    resp = client.post(f"/onboarding/review/{pid}/action", json={"action": "dismiss"})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    db_session.refresh(org)
    assert "funding_stage" not in profile.read(org)


def test_review_action_unknown_proposal_is_404(app, db_session, make_org, client, login_as):
    org, _ = _logged_in(db_session, make_org, client, login_as, "notfound")

    resp = client.post("/onboarding/review/nope:nope/action", json={"action": "confirm"})

    assert resp.status_code == 404


def test_review_action_invalid_action_is_400(app, db_session, make_org, client, login_as):
    org, _ = _logged_in(db_session, make_org, client, login_as, "badaction")
    from app.modules.onboarding.services import profile, proposals

    profile.write(org, stage="growing")
    pid = proposals.proposal_id("funding_stage", "Series A or B")

    resp = client.post(f"/onboarding/review/{pid}/action", json={"action": "explode"})

    assert resp.status_code == 400


def test_gaps_screen_links_to_review_when_a_proposal_is_pending(
    app, db_session, make_org, client, login_as
):
    from app.modules.onboarding.services import profile

    org, _ = _logged_in(db_session, make_org, client, login_as, "gapslink", with_role=True)
    profile.write(org, stage="growing")

    resp = client.get("/onboarding/gaps")

    assert resp.status_code == 200, resp.get_data(as_text=True)
    html = resp.get_data(as_text=True)
    assert "/onboarding/review" in html
    assert "waiting for you to confirm or dismiss" in html


def test_company_screen_links_to_review_for_a_returning_user(
    app, db_session, make_org, client, login_as
):
    """A user who already finished onboarding reaches Screen 2 (Bring your
    company) from the dashboard's "Getting started" link (onboarding.index
    renders it for them); Review must be reachable from there too."""
    import datetime

    _make_default_role(db_session)
    org = make_org("returning")
    user = _make_user(db_session, org)
    user.onboarding_completed_at = datetime.datetime.utcnow()
    db_session.add(user)
    db_session.flush()
    login_as(client, user)

    resp = client.get("/onboarding/")

    assert resp.status_code == 200, resp.get_data(as_text=True)
    html = resp.get_data(as_text=True)
    assert 'href="/onboarding/review"' in html


def test_company_screen_has_no_review_link_for_a_new_user_mid_onboarding(
    app, db_session, make_org, client, login_as
):
    """The link only appears once onboarding is finished -- a first-time
    user on Screen 2 should not be sent to Review before they have answered
    anything Review could show."""
    org, _ = _logged_in(db_session, make_org, client, login_as, "notyet", with_role=True)

    resp = client.get("/onboarding/company")

    assert resp.status_code == 200, resp.get_data(as_text=True)
    html = resp.get_data(as_text=True)
    assert 'href="/onboarding/review"' not in html
