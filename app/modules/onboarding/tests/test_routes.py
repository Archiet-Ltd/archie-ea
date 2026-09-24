"""Onboarding routes and the profile store, against the shared db_session fixture."""
import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org, *, onboarding_completed=False):
    from app.models.user import User
    import uuid

    user = User(
        email=f"onboard-{uuid.uuid4().hex[:8]}@example.com",
        first_name="On",
        last_name="Board",
        confirmed=True,
        organization_id=org.id,
    )
    if onboarding_completed:
        import datetime
        user.onboarding_completed_at = datetime.datetime.utcnow()
    db_session.add(user)
    db_session.flush()
    return user




def test_profile_write_only_touches_the_onboarding_key(app, db_session, make_org):
    from app.modules.onboarding.services import profile

    org = make_org("profile")
    org.settings = {"other_key": "untouched"}
    db_session.add(org)
    db_session.flush()

    profile.write(org, stage="growing")

    assert org.settings["other_key"] == "untouched"
    assert org.settings["onboarding"]["stage"] == "growing"


def test_profile_write_merges_rather_than_replaces(app, db_session, make_org):
    from app.modules.onboarding.services import profile

    org = make_org("merge")
    profile.write(org, stage="pre_revenue")
    profile.write(org, company_size="5 people")

    assert org.settings["onboarding"]["stage"] == "pre_revenue"
    assert org.settings["onboarding"]["company_size"] == "5 people"


def test_index_redirects_to_welcome_for_a_genuinely_new_org(app, db_session, make_org, client, login_as):
    org = make_org("new")
    user = _make_user(db_session, org)
    login_as(client, user)
    resp = client.get("/onboarding/", follow_redirects=False)
    assert resp.status_code in (302, 308)
    assert resp.headers["Location"].endswith("/onboarding/welcome")


def test_index_redirects_to_first_question_when_org_already_onboarded(app, db_session, make_org, client, login_as):
    """An invited team member enters at Screen 3, per onboarding-prd-v1 §3."""
    from app.modules.onboarding.services import profile

    org = make_org("existing")
    profile.write(org, stage="growing")
    user = _make_user(db_session, org)
    login_as(client, user)
    resp = client.get("/onboarding/", follow_redirects=False)
    assert resp.status_code in (302, 308)
    assert resp.headers["Location"].endswith("/onboarding/first-question")


def test_index_shows_saved_company_answers_to_someone_who_already_finished(
    app, db_session, make_org, client, login_as
):
    """Finished users only reach /onboarding/ on purpose (All modules lists
    "Getting started"). It must answer 200 with their saved answers, not bounce."""
    from app.modules.onboarding.services import profile

    org = make_org("done")
    profile.write(org, stage="growing", company_size="40 people")
    user = _make_user(db_session, org, onboarding_completed=True)
    login_as(client, user)

    resp = client.get("/onboarding/", follow_redirects=False)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Bring your company" in html
    assert "40 people" in html


def test_gap_action_accept_keeps_the_gap_visible(app, db_session, make_org):
    """Redesign v3 §4/§5: accepted gaps are never counted as filled."""
    from app.modules.onboarding.routes import _recorded_for_org
    from app.modules.onboarding.services import profile

    org = make_org("accept")
    profile.write(org, stage="pre_revenue", accepted_gaps={
        "roles:founder_ceo": {"reason": "Not yet", "at": "2026-01-01T00:00:00", "by_user_id": 1}
    })
    recorded = _recorded_for_org(org)

    assert "founder_ceo" not in recorded["roles"], (
        "an accepted gap must stay a gap, not be treated as recorded/filled"
    )


def test_gap_action_assign_marks_the_gap_recorded(app, db_session, make_org):
    from app.modules.onboarding.routes import _recorded_for_org
    from app.modules.onboarding.services import profile

    org = make_org("assign")
    profile.write(org, stage="pre_revenue", assigned_gaps={
        "roles:founder_ceo": {"assignee": "Jo", "at": "2026-01-01T00:00:00", "by_user_id": 1}
    })
    recorded = _recorded_for_org(org)

    assert "founder_ceo" in recorded["roles"], "an assigned gap should count as recorded"


def test_website_field_saves_the_address_and_says_reading_is_not_available(
    app, db_session, make_org, client, login_as
):
    """Screen 2's website field posts here. It must answer 200 with an honest
    "not available yet" message and record the address on the organisation
    profile -- never 500, never a fabricated reading."""
    org = make_org("website")
    user = _make_user(db_session, org)
    login_as(client, user)

    resp = client.post(
        "/onboarding/api/website",
        json={"source_url": "https://example.com"},
    )

    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["data"]["status"] == "not_available_yet"
    from app.modules.onboarding.services import profile

    db_session.refresh(org)
    assert profile.read(org)["source_url"] == "https://example.com"


def test_website_field_without_an_address_is_a_400_not_a_500(
    app, db_session, make_org, client, login_as
):
    org = make_org("website-empty")
    user = _make_user(db_session, org)
    login_as(client, user)

    resp = client.post("/onboarding/api/website", json={"source_url": "   "})

    assert resp.status_code == 400


def _logged_in(db_session, make_org, client, login_as, name):
    org = make_org(name)
    user = _make_user(db_session, org)
    login_as(client, user)
    return org, user


def test_company_step_json_post_returns_the_next_screen(app, db_session, make_org, client, login_as):
    org, _ = _logged_in(db_session, make_org, client, login_as, "company-json")

    resp = client.post(
        "/onboarding/company",
        json={"stage": "early_revenue", "company_size": "8 people"},
    )

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["data"]["next"].endswith("/onboarding/first-question")
    from app.modules.onboarding.services import profile

    db_session.refresh(org)
    assert profile.read(org)["stage"] == "early_revenue"


def test_company_step_rejects_an_unknown_stage(app, db_session, make_org, client, login_as):
    _logged_in(db_session, make_org, client, login_as, "company-bad-stage")

    resp = client.post("/onboarding/company", json={"stage": "nonsense"})

    assert resp.status_code == 400


def test_first_question_may_be_skipped_and_still_advances(app, db_session, make_org, client, login_as):
    _logged_in(db_session, make_org, client, login_as, "first-question")

    resp = client.post("/onboarding/first-question", json={"answer": ""})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["data"]["next"].endswith("/onboarding/gaps")


def test_a_gap_can_be_accepted_with_a_reason_and_is_recorded(app, db_session, make_org, client, login_as):
    org, _ = _logged_in(db_session, make_org, client, login_as, "gap-accept")

    resp = client.post(
        "/onboarding/gaps/selling/action",
        json={"action": "accept", "reason": "Planned for Q1"},
    )

    assert resp.status_code == 200, resp.get_data(as_text=True)
    from app.modules.onboarding.services import profile

    db_session.refresh(org)
    assert profile.read(org)["accepted_gaps"]["selling"]["reason"] == "Planned for Q1"


def test_finishing_onboarding_records_completion_once_and_returns_the_dashboard(
    app, db_session, make_org, client, login_as
):
    _, user = _logged_in(db_session, make_org, client, login_as, "finish")
    assert user.onboarding_completed_at is None

    resp = client.post("/onboarding/finish", json={"enterprise_role": "cto"})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["data"]["next"].endswith("/dashboard/overview")
    db_session.refresh(user)
    assert user.onboarding_completed_at is not None
    assert user.enterprise_role == "cto"


def test_saved_answers_with_quotes_are_escaped_into_the_page_state(
    app, db_session, make_org, client, login_as
):
    """Screen 2 seeds its Alpine state from saved answers. A company size or
    industry containing an apostrophe or double quote must not terminate the
    x-data attribute or the JS string (which silently kills every control)."""
    from app.modules.onboarding.services import profile

    org, _ = _logged_in(db_session, make_org, client, login_as, "quotes")
    profile.write(org, stage="growing", company_size='Bob\'s "Co"', industry="Men's wear")

    resp = client.get("/onboarding/company")

    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'companySize: &#34;Bob' in html, "value must be a JSON string, HTML-escaped for the attribute"
    assert 'companySize: "Bob' not in html, "a raw double quote would end the x-data attribute early"
