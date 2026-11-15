"""The chat records and reads application support through the one module, and says so in plain words."""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

CAPABILITY = "Plans, Quotas and Payments"


def _world(db_session, make_org, applications=("Monelytics",)):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.user import Role, User

    org = make_org("chat-support")
    rows = [ApplicationComponent(name=name, organization_id=org.id) for name in applications]
    capability = BusinessCapability(name=CAPABILITY, code="CS-%s" % uuid.uuid4().hex[:6], organization_id=org.id, level=1)
    user = User(email="cs-%s@example.test" % uuid.uuid4().hex[:6], first_name="C", last_name="S",
                organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect")
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = uuid.uuid4().hex
    db_session.add_all(rows + [capability, user])
    db_session.flush()
    return org, rows, capability, user


def _call(user, **arguments):
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

    return ToolExecutor(user.id, persona="enterprise_architect").execute(
        ToolCall(id="t", name="link_application_to_capability", arguments=arguments))


def test_the_chat_records_support_and_every_reader_agrees(db_session, make_org, tenant_ctx):
    from app.services import capability_support as cs

    org, (app_row,), capability, user = _world(db_session, make_org)
    with tenant_ctx(org.id):
        result = _call(user, application_name="Monelytics", capability_name=CAPABILITY,
                       how="realizes", support_level="full")
    assert result["success"] is True
    message = result["message"]
    assert "Monelytics Realizes Plans, Quotas and Payments" in message and "Primary support" in message
    assert "/applications/%d" % app_row.id in message
    rows = cs.support_rows(org.id)
    assert [(r["kind"], r["support_level"]) for r in rows] == [("realization", "primary")]
    assert [r["capability_name"] for r in cs.capabilities_of_application(org.id, app_row.id)] == [CAPABILITY]


def test_a_second_request_for_the_same_pair_says_it_is_already_recorded(db_session, make_org, tenant_ctx):
    org, _rows, _cap, user = _world(db_session, make_org)
    with tenant_ctx(org.id):
        _call(user, application_name="Monelytics", capability_name=CAPABILITY)
        again = _call(user, application_name="Monelytics", capability_name=CAPABILITY)
    assert again["success"] is True and again["message"].startswith("Already recorded")


def test_a_capability_that_does_not_exist_is_refused_in_one_sentence(db_session, make_org, tenant_ctx):
    from app.services import capability_support as cs

    org, _rows, _cap, user = _world(db_session, make_org)
    with tenant_ctx(org.id):
        result = _call(user, application_name="Monelytics", capability_name="Zebra Licensing")
    assert result == {"success": False, "error": "No capability named 'Zebra Licensing' exists in this workspace."}
    assert cs.support_rows(org.id) == []


def test_a_name_two_applications_share_names_both_with_their_pages(db_session, make_org, tenant_ctx):
    org, rows, _cap, user = _world(db_session, make_org, applications=("Ifiok", "Ifiok"))
    with tenant_ctx(org.id):
        result = _call(user, application_name="Ifiok", capability_name=CAPABILITY)
    assert result["success"] is False
    for row in rows:
        assert "/applications/%d" % row.id in result["error"]
    assert result["error"].count("Ifiok") >= 3


def test_a_failure_while_saving_never_shows_database_text(db_session, make_org, tenant_ctx, monkeypatch):
    org, _rows, _cap, user = _world(db_session, make_org)

    def boom(*args, **kwargs):
        raise RuntimeError('null value in column "organization_id" of relation "x" violates not-null constraint')

    monkeypatch.setattr("app.models.archimate_relationship_sync._ensure_relationship", boom)
    with tenant_ctx(org.id):
        result = _call(user, application_name="Monelytics", capability_name=CAPABILITY)
    assert result["success"] is False
    assert result["error"] == "The link could not be saved, so nothing was changed."
    for word in ("relation", "column", "violates", "constraint", "SQL"):
        assert word not in result["error"]


def test_the_review_declines_an_impossible_request_before_any_model_is_asked(db_session, make_org):
    from app.modules.ai_chat.services import change_review

    org, _rows, _cap, _user = _world(db_session, make_org)
    assert "link_application_to_capability" in change_review._TOOL_TARGETS
    lookup = change_review._TOOL_TARGETS["link_application_to_capability"]
    missing = lookup({"application_name": "Monelytics", "capability_name": "Zebra Licensing"}, org.id)
    assert missing.ok is False and missing.reason == "No capability named 'Zebra Licensing' exists in this workspace."
    good = lookup({"application_name": "Monelytics", "capability_name": CAPABILITY, "support_level": "full"}, org.id)
    assert good.ok is True and good.checked is not False
    assert good.facts == {"application": "Monelytics", "capability": CAPABILITY, "how": "Realizes",
                          "support_level": "primary"}
    assert good.before["kind"] == "create"


def test_another_organisations_names_are_not_resolved_by_the_review(db_session, make_org):
    from app.modules.ai_chat.services import change_review

    org, _rows, _cap, _user = _world(db_session, make_org)
    other = make_org("chat-support-other")
    lookup = change_review._TOOL_TARGETS["link_application_to_capability"]
    refused = lookup({"application_name": "Monelytics", "capability_name": CAPABILITY}, other.id)
    assert refused.ok is False


def test_asking_who_supports_a_capability_never_makes_a_capability_record(db_session, make_org, tenant_ctx):
    from app.models.unified_capability import UnifiedCapability
    from app.services.archimate_backbone import create_backbone_element

    org, _rows, _cap, user = _world(db_session, make_org)
    create_backbone_element(element_type="Capability", layer="Strategy", name="Only Modelled", organization_id=org.id)
    count = lambda: db_session.query(UnifiedCapability).filter(UnifiedCapability.organization_id == org.id).count()  # noqa: E731
    before = count()
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

    with tenant_ctx(org.id):
        answer = ToolExecutor(user.id, persona="enterprise_architect").execute(
            ToolCall(id="t", name="find_applications_by_capability", arguments={"capability_name": "Only Modelled"}))
    assert answer["success"] is True and answer["result"] == []
    assert count() == before
