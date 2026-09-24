"""Onboarding routes and the profile store, against the shared db_session fixture."""
import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org, *, onboarding_completed=False, is_org_admin=False, is_platform_admin=False):
    from app.models.user import User
    import uuid

    user = User(
        email=f"onboard-{uuid.uuid4().hex[:8]}@example.com",
        first_name="On",
        last_name="Board",
        confirmed=True,
        organization_id=org.id,
        is_org_admin=is_org_admin,
        is_platform_admin=is_platform_admin,
    )
    # A real signup always ends up with a Role (Role.insert_roles() seeds a
    # default one and every account gets it) -- a raw User() row here, without
    # one, does not represent a reachable production state, but it does make
    # nav_macros.html's `current_user.role.index` crash on any page that
    # extends layouts/base.html (the wizard shell every onboarding screen
    # uses), which is not what these tests are about.
    from app.models.user import Permission, Role

    role = Role.query.filter_by(name="Architect").first()
    if role is None:
        role = Role(name="Architect", permissions=Permission.GENERAL, index="main", default=True)
        db_session.add(role)
        db_session.flush()
    user.role = role
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


# ── Workspace setup (restores what the retired first-login modal did) ──────


def test_company_screen_shows_the_role_picker_and_feature_cards(
    app, db_session, make_org, client, login_as
):
    """The "Set up your workspace" step lives on this same screen: role
    choice and the "Key features for you" cards the retired modal showed."""
    _logged_in(db_session, make_org, client, login_as, "workspace-fields")

    resp = client.get("/onboarding/company")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Set up your workspace" in html
    assert "What&#39;s your role?" in html or "What's your role?" in html
    assert "Key features for you" in html
    # The role -> card mapping itself, present via the shared partial.
    assert "roleCards:" in html
    assert "Architecture Journey" in html


def test_company_screen_offers_admin_setup_only_to_admins(
    app, db_session, make_org, client, login_as
):
    org = make_org("workspace-admin-hidden")
    user = _make_user(db_session, org, is_org_admin=False, is_platform_admin=False)
    login_as(client, user)

    resp = client.get("/onboarding/company")
    html = resp.get_data(as_text=True)

    assert "Set up your AI provider" not in html
    assert "Invite your teammates" not in html


def test_company_screen_offers_admin_setup_to_an_org_admin(
    app, db_session, make_org, client, login_as
):
    org = make_org("workspace-admin-shown")
    user = _make_user(db_session, org, is_org_admin=True)
    login_as(client, user)

    resp = client.get("/onboarding/company")
    html = resp.get_data(as_text=True)

    assert "Set up your AI provider" in html
    assert "Invite your teammates" in html
    assert "/admin/api-settings" in html
    assert "/admin/invite-user" in html


def test_company_step_persists_the_chosen_role_without_completing_onboarding(
    app, db_session, make_org, client, login_as
):
    """Role choice moved to this screen has "the same effect" the retired
    modal's role choice did -- current_user.enterprise_role updates -- but
    must NOT mark onboarding complete early: three more screens remain."""
    org, user = _logged_in(db_session, make_org, client, login_as, "workspace-role")
    assert user.enterprise_role != "portfolio_manager"

    resp = client.post(
        "/onboarding/company",
        json={"stage": "growing", "enterprise_role": "portfolio_manager"},
    )

    assert resp.status_code == 200, resp.get_data(as_text=True)
    db_session.refresh(user)
    assert user.enterprise_role == "portfolio_manager"
    assert user.onboarding_completed_at is None, (
        "choosing a role at screen 2 must not complete onboarding -- "
        "screens 3-5 have not run yet"
    )


def test_company_step_ignores_an_unrecognised_role(app, db_session, make_org, client, login_as):
    org, user = _logged_in(db_session, make_org, client, login_as, "workspace-bad-role")
    before = user.enterprise_role

    resp = client.post(
        "/onboarding/company",
        json={"stage": "growing", "enterprise_role": "not-a-real-role"},
    )

    assert resp.status_code == 200, resp.get_data(as_text=True)
    db_session.refresh(user)
    assert user.enterprise_role == before


def test_chosen_role_is_reflected_in_the_sidebar_workspace_zone(
    app, db_session, make_org, client, login_as
):
    """"the dashboard mode reflects it": the same enterprise_role write the
    old modal made, so get_sidebar_zones() -- the dashboard's own single
    source of truth for role-based personalisation -- picks it up."""
    from app.utils.role_access import get_sidebar_zones

    org, user = _logged_in(db_session, make_org, client, login_as, "workspace-sidebar")

    resp = client.post(
        "/onboarding/company",
        json={"stage": "growing", "enterprise_role": "business_architect"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    db_session.refresh(user)
    assert user.enterprise_role == "business_architect"

    zones = get_sidebar_zones(user)
    my_work_labels = [
        link["label"] for zone in zones if zone["zone"] == "my_work" for link in zone["links"]
    ]
    assert "Value Streams" in my_work_labels, (
        "get_sidebar_zones must reflect the role chosen during onboarding, "
        "the same way it did for the role the retired modal wrote"
    )


def test_api_role_updates_role_only_not_completion(app, db_session, make_org, client, login_as):
    """The standalone workspace-setup page has no stage/company fields to
    bundle the role write with, unlike screen 2 -- this is its endpoint."""
    org, user = _logged_in(db_session, make_org, client, login_as, "api-role")

    resp = client.post("/onboarding/api/role", json={"enterprise_role": "cto"})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["data"]["applied"] is True
    db_session.refresh(user)
    assert user.enterprise_role == "cto"
    assert user.onboarding_completed_at is None


def test_workspace_setup_page_is_reachable_after_onboarding(
    app, db_session, make_org, client, login_as
):
    """Settings / the user menu link here (admin_header.html); this proves
    the destination itself answers, regardless of onboarding_completed_at."""
    org = make_org("workspace-setup-page")
    user = _make_user(db_session, org, onboarding_completed=True)
    login_as(client, user)

    resp = client.get("/onboarding/workspace-setup")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Workspace setup" in html
    assert "Key features for you" in html


def test_an_admin_reaches_ai_provider_setup_from_the_workspace_setup_link(
    app, db_session, make_org, client, login_as
):
    """The link the workspace-setup step renders for an admin
    (test_company_screen_offers_admin_setup_to_an_org_admin above) must lead
    somewhere real: a fully-provisioned org admin following it gets the AI
    provider setup page, not a 403."""
    from app.models.org_role import OrgRole
    from app.models.user import Permission, Role

    org = make_org("workspace-admin-reaches")
    user = _make_user(db_session, org, is_org_admin=True)
    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        admin_role = Role(name="Administrator", permissions=Permission.ADMINISTER, index="admin")
        db_session.add(admin_role)
        db_session.flush()
    user.role = admin_role
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "org_admin")
    db_session.flush()
    login_as(client, user)

    resp = client.get("/admin/api-settings")

    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]


def test_twin_screen_renders_and_shows_the_role_chosen_earlier(
    app, db_session, make_org, client, login_as
):
    """Role choice moved to screen 2; screen 5 no longer asks again -- it
    must still render, using whatever role screen 2 (or a prior visit to
    workspace-setup) already saved."""
    org, user = _logged_in(db_session, make_org, client, login_as, "twin-role")
    from app.modules.onboarding.services import completion

    completion.set_role(user, "cto")
    db_session.add(user)
    db_session.flush()

    resp = client.get("/onboarding/twin")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "selectedRole: 'cto'" in html
    assert "View Architecture Health" in html, "cto's primaryCTA label must be present"


def test_security_and_data_architect_roles_are_accepted_by_set_role(
    app, db_session, make_org, client, login_as
):
    """Finding 5: security_architect and data_architect appear in the role
    picker UI but were silently dropped by completion.set_role -- they must
    now be accepted."""
    org, user = _logged_in(db_session, make_org, client, login_as, "sec-data-role")
    from app.modules.onboarding.services import completion

    assert completion.set_role(user, "security_architect") is True
    assert user.enterprise_role == "security_architect"

    assert completion.set_role(user, "data_architect") is True
    assert user.enterprise_role == "data_architect"

    # An unknown role must still be rejected.
    assert completion.set_role(user, "not_a_real_role") is False
    assert user.enterprise_role == "data_architect"


def test_skip_marks_onboarding_complete_and_goes_to_the_dashboard(
    app, db_session, make_org, client, login_as
):
    org = make_org("skip")
    user = _make_user(db_session, org)
    login_as(client, user)
    assert user.onboarding_completed_at is None

    resp = client.get("/onboarding/skip", follow_redirects=False)

    assert resp.status_code in (302, 308)
    assert resp.headers["Location"].endswith("/dashboard/overview")
    db_session.refresh(user)
    assert user.onboarding_completed_at is not None


def test_skip_breaks_the_redirect_loop_for_a_platform_admin_with_an_empty_workspace(
    app, db_session, make_org, client, login_as
):
    """A platform admin with nothing to onboard must never be trapped: after
    Skip, /dashboard/overview must not bounce back into onboarding even
    though the workspace is still empty -- dashboard.overview's own redirect
    is gated on `not onboarding_completed_at`, which Skip has just set."""
    org = make_org("skip-loop")
    user = _make_user(db_session, org, is_platform_admin=True)
    user.enterprise_role = "platform_admin"
    db_session.add(user)
    db_session.flush()
    login_as(client, user)

    skip_resp = client.get("/onboarding/skip", follow_redirects=False)
    assert skip_resp.status_code in (302, 308)
    assert skip_resp.headers["Location"].endswith("/dashboard/overview")

    dashboard_resp = client.get("/dashboard/overview", follow_redirects=False)

    assert dashboard_resp.status_code == 200, (
        "a platform admin who just skipped onboarding must reach the "
        "dashboard, not be redirected straight back into onboarding"
    )
