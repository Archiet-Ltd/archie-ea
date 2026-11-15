"""AI questions are counted in the organisation they were asked in, not the asker's home one."""
import pytest

from tests.test_plan_usage import _ask, _org, _page, _text, _user

pytestmark = pytest.mark.usefixtures("db_session")


def test_a_person_in_two_organisations_is_counted_in_each_for_what_they_asked_there(
    app, db_session, client, login_as
):
    from app.services import billing_plans

    one, two = _org(db_session, "ca"), _org(db_session, "cb")
    admin_one = _user(db_session, one, admin=True, role_name="Administrator")
    admin_two = _user(db_session, two, admin=True, role_name="Administrator")
    shared = _user(db_session, one)  # home organisation is A, also works in B
    _ask(db_session, shared, 3, org=one)
    _ask(db_session, shared, 2, org=two)
    db_session.commit()

    assert billing_plans.ai_usage_status(one.id)["used"] == 3
    assert billing_plans.ai_usage_status(two.id)["used"] == 2
    assert _text(_page(app, client, login_as, admin_one), "property-ai-used") == "3"
    assert _text(_page(app, client, login_as, admin_two), "property-ai-used") == "2"
