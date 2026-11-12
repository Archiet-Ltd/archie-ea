"""The Controls page shows the checks that really ran on the assistant's changes, for the organisation only.

A change the assistant makes is checked by the review loop. The Controls page counts those checks, shows each
one's outcome and says which kinds of AI operation are covered. Each organisation sees its own checks and
nothing else. The reviewer is stubbed at the router, as in the change review tests.
"""

import uuid

import pytest

from tests.test_change_review import PASS_ALL, FakeReviewer, _application, _ask_for_status, _user

CONTROLS = "/policy-monitoring/"


def _viewer(db_session, org):
    """An ordinary architect of the organisation: opens Controls through the page's own role rule."""
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Architect").first()
    user = User(email="controls-%s@example.com" % uuid.uuid4().hex[:10], first_name="Con", last_name="Trols",
                organization_id=org.id, role=role, enterprise_role="enterprise_architect", confirmed=True)
    user.password = "Controls!12345"
    db_session.add(user)
    db_session.flush()
    return user


def _two_organisations(db_session, make_org, tenant_ctx, monkeypatch):
    """Organisation A has one change applied; organisation B has one declined by the checks."""
    org_a, org_b = make_org("controls-a"), make_org("controls-b")
    user_a, user_b = _user(db_session, org_a.id), _user(db_session, org_b.id)
    tag = uuid.uuid4().hex[:6]
    alpha = _application(db_session, org_a.id, "Alpha Ledger %s" % tag, "development")
    bravo = _application(db_session, org_b.id, "Bravo Billing %s" % tag, "production")
    FakeReviewer([PASS_ALL, PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org_a.id):
        _ask_for_status(user_a, alpha.name, "production")
    with tenant_ctx(org_b.id):
        # Bravo is already in production: the checks decline the change before any model is asked.
        _ask_for_status(user_b, bravo.name, "production")
    return org_a, org_b, alpha, bravo


def test_the_checks_that_ran_are_counted_with_their_outcome_for_one_organisation_only(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app.modules.ai_chat.services.change_review import checks_report

    org_a, org_b, alpha, bravo = _two_organisations(db_session, make_org, tenant_ctx, monkeypatch)

    with tenant_ctx(org_a.id):
        report_a = checks_report(org_a.id)
    assert report_a["total"] == 1
    assert (report_a["checks_passed"], report_a["checks_failed"]) == (1, 0)
    assert (report_a["applied"], report_a["declined"], report_a["in_review"]) == (1, 0, 0)
    assert [r["subject"] for r in report_a["recent"]] == [alpha.name]
    assert report_a["recent"][0]["result"] == "Applied"

    with tenant_ctx(org_b.id):
        report_b = checks_report(org_b.id)
    assert report_b["total"] == 1
    assert (report_b["checks_passed"], report_b["checks_failed"]) == (0, 1)
    assert (report_b["applied"], report_b["declined"]) == (0, 1)
    assert [r["subject"] for r in report_b["recent"]] == [bravo.name]
    assert report_b["recent"][0]["result"] == "Declined"
    assert report_b["recent"][0]["reason"]


def test_an_organisation_with_no_changes_reports_zero(db_session, make_org, tenant_ctx):
    from app.modules.ai_chat.services.change_review import checks_report

    org = make_org("controls-empty")
    with tenant_ctx(org.id):
        report = checks_report(org.id)
    assert report["total"] == 0 and report["recent"] == []


def test_the_controls_page_counts_the_change_that_went_through_the_loop_and_hides_the_other_organisations(
    app, db_session, make_org, tenant_ctx, monkeypatch, client, login_as
):
    org_a, org_b, alpha, bravo = _two_organisations(db_session, make_org, tenant_ctx, monkeypatch)
    login_as(client, _viewer(db_session, org_a))

    response = client.get(CONTROLS)
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert 'data-testid="policy-checked-value"' in html
    assert alpha.name in html, "the change that was checked is listed by its subject"
    assert "Applied" in html
    assert bravo.name not in html, "another organisation's check never appears"
    assert "No AI operations checked yet" not in html
    # What is covered is stated, and matches what the code checks.
    coverage = html[html.index('data-testid="policy-coverage"'):]
    assert "Changes the assistant makes are checked" in coverage
    assert "answers in the chat are not checked by a policy yet" in coverage.lower()


def test_the_second_organisation_sees_its_own_declined_check(
    app, db_session, make_org, tenant_ctx, monkeypatch, client, login_as
):
    org_a, org_b, alpha, bravo = _two_organisations(db_session, make_org, tenant_ctx, monkeypatch)
    login_as(client, _viewer(db_session, org_b))

    html = client.get(CONTROLS).get_data(as_text=True)
    assert bravo.name in html and "Declined" in html
    assert alpha.name not in html


def test_an_organisation_where_nothing_ran_is_told_so_and_is_not_told_enforcement_is_strict(
    app, db_session, make_org, client, login_as
):
    org = make_org("controls-quiet")
    login_as(client, _viewer(db_session, org))

    html = client.get(CONTROLS).get_data(as_text=True)
    assert "No changes have been checked in the last 30 days" in html
    assert "in strict mode" not in html
    assert "policy-coverage" in html, "the page still says which operations it covers"


def test_the_pause_switch_is_what_the_page_says_about_whether_changes_are_checked(
    app, db_session, make_org, client, login_as
):
    from app.models.agent_oversight_state import AgentOversightState

    org = make_org("controls-paused")
    db_session.add(AgentOversightState(organization_id=org.id, writes_paused=True))
    db_session.flush()
    login_as(client, _viewer(db_session, org))

    html = client.get(CONTROLS).get_data(as_text=True)
    assert "Changes by the assistant are paused for this workspace" in html


def test_one_route_serves_controls_and_the_old_address_goes_there(app, db_session, make_org, client, login_as):
    login_as(client, _viewer(db_session, make_org("controls-route")))

    old = client.get("/enterprise/policy-monitoring")
    assert old.status_code in (301, 302, 303, 307, 308)
    assert old.headers["Location"].endswith(CONTROLS)
    rules = [r.rule for r in app.url_map.iter_rules() if r.endpoint.endswith("policy_dashboard")]
    assert rules == [CONTROLS]
