"""The plan page tells an organisation's administrator the plan, the people used, the AI used and whose AI answers."""
import re
import uuid
from datetime import datetime

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Usage {label} {suffix}", slug=f"usage-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, *, admin=False, role_name="User"):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    if Role.query.filter_by(name=role_name).first() is None:
        Role.insert_roles()
    user = User(email=f"usage-{uuid.uuid4().hex[:10]}@example.com", first_name="Una", last_name="Usage",
                organization_id=org.id, role=Role.query.filter_by(name=role_name).first(),
                is_org_admin=admin, confirmed=True)
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    if admin:
        OrgRole.set_role(org.id, user.id, "org_admin", granted_by_id=user.id)
    return user


def _ask(db_session, user, questions, answers=1, org=None):
    from app.models.conversation import ConversationMessageRecord, ConversationThreadRecord

    now = datetime.utcnow()
    thread = ConversationThreadRecord(id=str(uuid.uuid4()), user_id=user.id, title="t", model="m",
                                      organization_id=org.id if org is not None else user.organization_id, created_at=now, updated_at=now)
    db_session.add(thread)
    db_session.flush()
    for i in range(questions):
        db_session.add(ConversationMessageRecord(id=str(uuid.uuid4()), thread_id=thread.id, role="user",
                                                 content="q%d" % i, created_at=now))
    for i in range(answers):
        db_session.add(ConversationMessageRecord(id=str(uuid.uuid4()), thread_id=thread.id, role="assistant",
                                                 content="a%d" % i, created_at=now))
    db_session.flush()


def _text(html, testid):
    m = re.search(r'data-testid="%s"[^>]*>(.*?)</' % testid, html, re.S)
    return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else None


def _labels(sidebar_for, user):
    return [i.label for i in sidebar_for(user)["user_menu"]]


def _page(app, client, login_as, user):
    with app.app_context():
        login_as(client, user)
        return client.get("/admin/billing/").get_data(as_text=True)


def test_figures_are_the_counts_the_plan_enforces(app, db_session, client, login_as):
    from app.services import billing_plans

    org = _org(db_session, "one")
    admin = _user(db_session, org, admin=True, role_name="Administrator")
    member = _user(db_session, org)
    _ask(db_session, admin, 2)
    _ask(db_session, member, 1)
    db_session.commit()

    html = _page(app, client, login_as, admin)
    status = billing_plans.user_limit_status(org.id)
    assert _text(html, "billing-people-limit") == "%d of %d people" % (status["used"], status["limit"])
    assert _text(html, "property-ai-used") == str(billing_plans.ai_usage_status(org.id)["used"]) == "3"
    assert _text(html, "property-ai-allowance") == "No limit on your plan"
    assert "The built-in AI" in _text(html, "property-ai-provider")


def test_own_key_names_its_provider(app, db_session, client, login_as):
    from app.models.models import APISettings

    org = _org(db_session, "key")
    admin = _user(db_session, org, admin=True, role_name="Administrator")
    db_session.add(APISettings(organization_id=org.id, provider="anthropic", enabled=True))
    db_session.commit()
    html = _page(app, client, login_as, admin)
    assert "Your own Anthropic key" in _text(html, "property-ai-provider")


def test_second_organisation_sees_only_its_own_figures(app, db_session, client, login_as):
    from app.models.models import APISettings

    one, two = _org(db_session, "a"), _org(db_session, "b")
    admin_one = _user(db_session, one, admin=True, role_name="Administrator")
    admin_two = _user(db_session, two, admin=True, role_name="Administrator")
    _ask(db_session, admin_one, 5)
    _ask(db_session, admin_two, 2)
    db_session.add(APISettings(organization_id=one.id, provider="openai", enabled=True))
    db_session.commit()

    page_one = _page(app, client, login_as, admin_one)
    page_two = _page(app, client, login_as, admin_two)
    assert _text(page_one, "property-ai-used") == "5" and "OpenAI" in _text(page_one, "property-ai-provider")
    assert _text(page_two, "property-ai-used") == "2" and "The built-in AI" in _text(page_two, "property-ai-provider")


def test_account_menu_offers_plan_and_billing_to_an_organisation_administrator_only(app, db_session):
    from app.navigation.registry import sidebar_for

    org = _org(db_session, "menu")
    admin = _user(db_session, org, admin=True)  # administers by organisation role alone
    member = _user(db_session, org)
    db_session.commit()
    with app.test_request_context():
        from flask import g

        g.current_org_id = org.id
        assert "Plan and billing" in _labels(sidebar_for, admin)
        assert "Plan and billing" not in _labels(sidebar_for, member)


def test_settings_is_not_found_for_an_organisation_administrator(app, db_session, client, login_as):
    org = _org(db_session, "settings")
    admin = _user(db_session, org, admin=True, role_name="Administrator")
    db_session.commit()
    with app.app_context():
        login_as(client, admin)
        response = client.get("/settings")
    assert response.status_code == 404
    assert "Ask an administrator" not in response.get_data(as_text=True)


def test_a_viewer_is_refused_the_billing_pages(app, db_session, client, login_as):
    org = _org(db_session, "viewer")
    viewer = _user(db_session, org, role_name="Viewer")
    db_session.commit()
    with app.app_context():
        login_as(client, viewer)
        assert client.get("/admin/billing/").status_code == 403
