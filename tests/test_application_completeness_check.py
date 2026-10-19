"""The completeness check for the applications an application manager owns."""

import datetime
import uuid

import pytest

from app.services.inventory_completeness_service import (
    STALE_AFTER_DAYS,
    check_application,
    check_applications,
)

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, role="application_manager"):
    from app.models.user import User

    user = User(
        email=f"complete-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Com",
        last_name="Plete",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _app(db_session, org, name, **fields):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org.id, **fields)
    db_session.add(row)
    db_session.flush()
    return row


def _own(db_session, org, user, application):
    from app.models.application_owner import ApplicationOwner

    db_session.add(ApplicationOwner(
        application_id=application.id, user_id=user.id, organization_id=org.id,
        ownership_type="primary"))
    db_session.flush()


FULL = dict(description="Does things", lifecycle_status="operational", deployment_status="production",
            business_owner="Ada", technical_owner="Grace")


@pytest.fixture
def world(db_session, make_org):
    org_a, org_b = make_org("complete-a"), make_org("complete-b")
    return {"a": (org_a, _user(db_session, org_a)), "b": (org_b, _user(db_session, org_b))}


def test_complete_recent_application_has_no_finding(world, db_session):
    org, _ = world["a"]
    row = _app(db_session, org, "Complete", **FULL)
    assert check_applications([row]) == []


def test_missing_facts_are_named(world, db_session):
    org, _ = world["a"]
    row = _app(db_session, org, "Thin", description="  ", business_owner="Ada", deployment_status="production",
               lifecycle_status="operational")
    finding = check_application(row)
    assert finding["missing"] == ["Description", "Technical owner"]


def test_stale_record_is_reported_with_its_age(world, db_session):
    org, _ = world["a"]
    row = _app(db_session, org, "Old", **FULL)
    row.updated_at = datetime.datetime.utcnow() - datetime.timedelta(days=STALE_AFTER_DAYS + 10)
    db_session.flush()
    finding = check_application(row)
    assert finding["stale"] and finding["age_days"] >= STALE_AFTER_DAYS + 10
    assert check_applications([row])


def test_record_exactly_at_the_limit_is_not_stale(world, db_session):
    org, _ = world["a"]
    row = _app(db_session, org, "Edge", **FULL)
    row.updated_at = datetime.datetime.utcnow() - datetime.timedelta(days=STALE_AFTER_DAYS)
    db_session.flush()
    assert check_application(row)["stale"] is False


def test_unrecorded_change_date_is_not_called_stale(world, db_session):
    org, _ = world["a"]
    row = _app(db_session, org, "Undated", **FULL)
    row.updated_at = None
    finding = check_application(row)
    assert finding["age_unknown"] and not finding["stale"]


def test_findings_are_ordered_most_missing_first(world, db_session):
    org, _ = world["a"]
    one = _app(db_session, org, "One gap", **{**FULL, "description": None})
    three = _app(db_session, org, "Three gaps", **{**FULL, "description": None, "business_owner": None,
                                                    "technical_owner": None})
    assert [f["name"] for f in check_applications([one, three])] == ["Three gaps", "One gap"]


def test_page_lists_only_my_applications_and_links_to_the_fix(world, db_session, client, login_as):
    org, user = world["a"]
    mine = _app(db_session, org, "Mine thin", **{**FULL, "technical_owner": None})
    theirs = _app(db_session, org, "Someone elses thin", **{**FULL, "technical_owner": None})
    _own(db_session, org, user, mine)
    login_as(client, user)
    page = client.get("/my-applications/completeness").get_data(as_text=True)
    assert "Mine thin" in page and "Technical owner" in page
    assert "/my-applications/app/%d/edit" % mine.id in page
    assert "Someone elses thin" not in page
    assert theirs.id != mine.id


def test_other_organisation_sees_none_of_it(world, db_session, client, login_as):
    org_a, user_a = world["a"]
    org_b, user_b = world["b"]
    mine = _app(db_session, org_a, "Org A thin", **{**FULL, "description": None})
    _own(db_session, org_a, user_a, mine)
    login_as(client, user_b)
    page = client.get("/my-applications/completeness").get_data(as_text=True)
    assert "Org A thin" not in page
    assert "do not own any applications" in page


def test_nothing_to_fix_says_so(world, db_session, client, login_as):
    org, user = world["a"]
    row = _app(db_session, org, "All good", **FULL)
    _own(db_session, org, user, row)
    login_as(client, user)
    page = client.get("/my-applications/completeness").get_data(as_text=True)
    assert "Nothing to fix" in page


def test_dashboard_links_to_the_check(world, db_session, client, login_as):
    org, user = world["a"]
    login_as(client, user)
    page = client.get("/my-applications/").get_data(as_text=True)
    assert "/my-applications/completeness" in page
