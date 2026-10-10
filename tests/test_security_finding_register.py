"""Security finding register: lifecycle, honest states, platform-level reads, access.

The register holds facts about the platform, so two organisations must see the
same findings; and a finding can only be closed by a passing re-test.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

TRACKER = "/trust-centre/security-findings/"
SUMMARY = "/trust-centre/closed-findings"


@pytest.fixture(autouse=True)
def _empty_register(db_session):
    """The register is platform-level, so rows committed by an earlier test (or the
    live-server journey) are visible to every test. Clear it inside this test's
    transaction; the rollback at teardown restores whatever was there."""
    from app.models.security_finding import SecurityFinding

    db_session.query(SecurityFinding).delete()
    db_session.flush()
    yield


def _user(db_session, org, role):
    from app.models.user import Role, User

    user = User(
        email=f"{role}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=role,
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()
    role_row = Role.query.filter(Role.name.in_(("Administrator", "Admin"))).first()
    if role_row is None:
        role_row = Role(name="Administrator")
        db_session.add(role_row)
        db_session.flush()
    if role == "platform_admin":
        user.role = role_row
    db_session.commit()
    return user


def _form(**over):
    data = {
        "title": "Reflected script in search box",
        "severity": "high",
        "source": "internal_scan",
        "source_reference": "ZAP rule 40012",
        "discovered_on": "2026-09-01",
        "description": "Input echoed without encoding.",
    }
    data.update(over)
    return data


@pytest.fixture
def orgs(db_session, make_org):
    return make_org("a"), make_org("b")


@pytest.fixture
def architect_a(db_session, orgs):
    return _user(db_session, orgs[0], "security_architect")


@pytest.fixture
def architect_b(db_session, orgs):
    return _user(db_session, orgs[1], "security_architect")


def _finding(db_session, user, **over):
    from app.modules.trust_centre import services

    return services.record_finding(_form(**over), user)


def test_empty_register_says_why_and_shows_no_zero(client, login_as, architect_a):
    from app.models.security_finding import SecurityFinding

    assert SecurityFinding.query.count() == 0
    login_as(client, architect_a)
    page = client.get(TRACKER).get_data(as_text=True)
    assert "No findings recorded" in page
    assert 'data-testid="finding-count-' not in page
    login_as(client, architect_a)
    summary = client.get(SUMMARY).get_data(as_text=True)
    assert "No closed findings published" in summary
    assert 'data-testid="closed-by-severity"' not in summary


def test_lifecycle_open_fixed_retested_closed_published(db_session, architect_a):
    from app.modules.trust_centre import services

    f = _finding(db_session, architect_a)
    assert f.status == "open" and not f.fixed_without_retest

    with pytest.raises(services.FindingError):
        services.record_retest(f, True, "2026-09-10", architect_a)  # no fix yet
    with pytest.raises(services.FindingError):
        services.set_published(f, True, architect_a)  # not closed

    services.link_fix(f, "https://github.com/example/repo/pull/12", architect_a)
    assert f.status == "fixed_unretested" and f.fixed_without_retest
    with pytest.raises(services.FindingError):
        services.set_published(f, True, architect_a)  # fixed but not re-tested

    when = (date.today() + timedelta(days=3)).isoformat()
    services.schedule_retest(f, when, architect_a)
    assert f.retest_scheduled_on.isoformat() == when

    services.record_retest(f, True, "2026-09-10", architect_a)
    assert f.status == "closed" and f.closed_on == date(2026, 9, 10)
    assert not f.fixed_without_retest

    services.set_published(f, True, architect_a)
    assert f.published_at is not None
    assert [x.id for x in services.published_summary()["findings"]] == [f.id]


def test_failed_retest_reopens_and_needs_a_new_fix(db_session, architect_a):
    from app.modules.trust_centre import services

    f = _finding(db_session, architect_a)
    services.link_fix(f, "https://github.com/example/repo/pull/12", architect_a)
    services.record_retest(f, False, "2026-09-10", architect_a)
    assert f.status == "open" and f.retest_result == "failed"
    with pytest.raises(services.FindingError):
        services.record_retest(f, True, "2026-09-11", architect_a)
    services.link_fix(f, "https://github.com/example/repo/pull/13", architect_a)
    assert f.status == "fixed_unretested" and f.retest_result is None


@pytest.mark.parametrize("bad", ["", "javascript:alert(1)", "ftp://x/y", "not a url"])
def test_fix_link_must_be_a_web_address(db_session, architect_a, bad):
    from app.modules.trust_centre import services

    f = _finding(db_session, architect_a)
    with pytest.raises(services.FindingError):
        services.link_fix(f, bad, architect_a)
    assert f.fix_pr_url is None and f.status == "open"


def test_record_validation(db_session, architect_a):
    from app.modules.trust_centre import services

    for bad in ({"title": " "}, {"severity": "urgent"}, {"source": "gut feel"},
                {"discovered_on": ""}, {"discovered_on": "01/09/2026"}):
        with pytest.raises(services.FindingError):
            services.record_finding(_form(**bad), architect_a)


def test_sources_are_kept_apart_in_the_summary(db_session, architect_a):
    from app.modules.trust_centre import services

    f = _finding(db_session, architect_a, source="manual_checklist")
    services.link_fix(f, "https://github.com/example/repo/pull/1", architect_a)
    services.record_retest(f, True, "2026-09-10", architect_a)
    services.set_published(f, True, architect_a)
    by_source = dict(services.published_summary()["by_source"])
    assert by_source["Manual checklist (ASVS Level 2)"] == 1
    assert by_source["Internal scan (OWASP ZAP baseline and authenticated)"] is None
    assert by_source["External firm's report"] is None


def test_tracker_and_summary_render_flag_and_closed_states(client, login_as, db_session, architect_a):
    from app.modules.trust_centre import services

    fixed = _finding(db_session, architect_a, title="Fixed but untested")
    services.link_fix(fixed, "https://github.com/example/repo/pull/2", architect_a)
    closed = _finding(db_session, architect_a, title="Closed and published")
    services.link_fix(closed, "https://github.com/example/repo/pull/3", architect_a)
    services.record_retest(closed, True, "2026-09-10", architect_a)
    services.set_published(closed, True, architect_a)

    login_as(client, architect_a)
    page = client.get(TRACKER).get_data(as_text=True)
    assert f'data-testid="finding-unretested-{fixed.id}"' in page
    assert f'data-testid="finding-unretested-{closed.id}"' not in page
    login_as(client, architect_a)
    summary = client.get(SUMMARY).get_data(as_text=True)
    assert "Closed and published" in summary
    assert "Fixed but untested" not in summary


def test_two_organisations_see_the_same_register(client, login_as, db_session, architect_a, architect_b):
    from app.modules.trust_centre import services

    f = _finding(db_session, architect_a, title="Shared platform finding")
    services.link_fix(f, "https://github.com/example/repo/pull/4", architect_a)
    services.record_retest(f, True, "2026-09-10", architect_a)
    services.set_published(f, True, architect_a)

    seen = {}
    for label, user in (("a", architect_a), ("b", architect_b)):
        login_as(client, user)
        seen[label] = (
            "Shared platform finding" in client.get(TRACKER).get_data(as_text=True),
            None,
        )
        login_as(client, user)
        seen[label] = (seen[label][0], "Shared platform finding" in client.get(SUMMARY).get_data(as_text=True))
    assert seen["a"] == seen["b"] == (True, True)


def test_audit_entry_is_written_against_the_acting_organisation(db_session, orgs, architect_a):
    from app.models.audit_log import AuditLog

    f = _finding(db_session, architect_a)
    rows = AuditLog.query.filter_by(table_name="security_findings", record_id=f.id).all()
    assert [(r.organization_id, r.user_id, r.action) for r in rows] == [
        (orgs[0].id, architect_a.id, "finding_record")
    ]
    assert not AuditLog.query.filter_by(table_name="security_findings", organization_id=orgs[1].id).count()


def test_only_readers_of_the_audit_trail_reach_the_tracker(client, login_as, db_session, orgs, architect_a):
    other = _user(db_session, orgs[0], "enterprise_architect")
    admin = _user(db_session, orgs[0], "platform_admin")
    f = _finding(db_session, architect_a)

    login_as(client, other)
    assert client.get(TRACKER).status_code == 403
    login_as(client, other)
    assert client.post(TRACKER, data=_form()).status_code == 403
    login_as(client, other)
    assert client.post(f"{TRACKER}{f.id}/fix", data={"fix_pr_url": "https://x.example/p/1"}).status_code == 403
    login_as(client, admin)
    assert client.get(TRACKER).status_code == 200
    client.get("/account/logout")


def test_anonymous_is_sent_to_sign_in(client):
    resp = client.get(TRACKER)
    assert resp.status_code in (301, 302, 401)
    assert client.get(SUMMARY).status_code in (301, 302, 401)


def test_post_routes_record_and_change_state(client, login_as, db_session, architect_a):
    from app.models.security_finding import SecurityFinding

    login_as(client, architect_a)
    assert client.post(TRACKER, data=_form(title="Posted finding")).status_code == 302
    f = SecurityFinding.query.filter_by(title="Posted finding").one()
    login_as(client, architect_a)
    client.post(f"{TRACKER}{f.id}/fix", data={"fix_pr_url": "https://github.com/example/repo/pull/9"})
    login_as(client, architect_a)
    client.post(f"{TRACKER}{f.id}/schedule-retest", data={"retest_scheduled_on": "2026-10-01"})
    login_as(client, architect_a)
    client.post(f"{TRACKER}{f.id}/record-retest", data={"retested_on": "2026-10-01", "result": "passed"})
    login_as(client, architect_a)
    client.post(f"{TRACKER}{f.id}/publish", data={"publish": "1"})
    db_session.refresh(f)
    assert f.status == "closed" and f.published_at is not None
    login_as(client, architect_a)
    assert client.post(f"{TRACKER}999999/fix", data={"fix_pr_url": "https://x.example/p"}).status_code == 404


def test_directory_offers_the_tracker_only_to_those_who_can_open_it(
    client, login_as, db_session, make_org, architect_a
):
    """The All-modules directory must not advertise a destination that answers 403."""
    admin = _user(db_session, make_org("dir"), "platform_admin")
    seen = {}
    for label, user in (
        ("security_architect", architect_a),
        ("platform_admin", admin),
        ("enterprise_architect", _user(db_session, make_org("dir"), "enterprise_architect")),
        ("solution_architect", _user(db_session, make_org("dir"), "solution_architect")),
        ("business_architect", _user(db_session, make_org("dir"), "business_architect")),
    ):
        login_as(client, user)
        seen[label] = TRACKER in client.get("/modules/").get_data(as_text=True)
    assert seen == {
        "security_architect": True,
        "platform_admin": True,
        "enterprise_architect": False,
        "solution_architect": False,
        "business_architect": False,
    }
