"""Impact preview in the approval inbox, owner notification on a new
proposal, and the decided list that keeps a rejection's reason.

The approval queue is AIChatApprovalService; the impact answer is the one
engine's (IntelligenceQueryService.cross_layer_impact) reached through
ArchitectureChangeImpactService. These tests pin:

* what the preview says for an application change, a capability change and the
  kinds of change that are not in the model (honest empty states);
* the three id spaces (ApplicationComponent, BusinessCapability, ArchiMate
  element, UnifiedCapability) are never mixed, with decoy rows whose ids
  collide;
* two organisations, an unset organisation, and a viewer;
* the query count does not grow with the rows;
* owners are notified in-app and by email on creation, and nobody else is.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import event

# Ids chosen to collide across the id spaces on purpose.
ROOT_ELEMENT_ID = 880_001
SHARED_ID = 880_002  # ApplicationComponent id of the real app == a decoy ELEMENT id
CAP_SHARED_ID = 880_003  # BusinessCapability id of the real capability == a decoy UnifiedCapability id


@pytest.fixture
def make_org(db_session):
    """Organisations on the contract plan: the default plan admits three people,
    and these tests need more owners than that."""

    def _make(label: str = "org"):
        from app.models.organization import Organization

        suffix = uuid.uuid4().hex[:10]
        org = Organization(name=f"Test {label} {suffix}", slug=f"test-{label}-{suffix}", plan="enterprise")
        db_session.add(org)
        db_session.flush()
        return org

    return _make


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def _user(db_session, org, label, *, can_approve=True, first=None, last="Person"):
    from app.models.user import Permission, Role, User

    suffix = uuid.uuid4().hex[:8]
    role_name = f"Impact Preview {label} {suffix}"
    role = Role(
        name=role_name,
        permissions=Permission.GENERAL if can_approve else 0,
        index="main",
        default=False,
    )
    db_session.add(role)
    db_session.flush()
    user = User(
        email=f"impact-{label}-{suffix}@example.com",
        first_name=first if first is not None else label,
        last_name=last,
        organization_id=org.id,
        confirmed=True,
        enterprise_role="application_manager",
    )
    user.role = role
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org_id, name, etype, *, element_id=None):
    from app.models import ArchiMateElement

    kwargs = {"id": element_id} if element_id is not None else {}
    layer = "application" if etype.startswith("Application") else "strategy"
    el = ArchiMateElement(name=name, type=etype, layer=layer, organization_id=org_id, **kwargs)
    db_session.add(el)
    db_session.flush()
    return el


def _serves(db_session, org_id, source, target):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type="Serving", organization_id=org_id
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def _component(db_session, org_id, element, name, *, comp_id=None):
    from app.models.application_portfolio import ApplicationComponent

    kwargs = {"id": comp_id} if comp_id is not None else {}
    comp = ApplicationComponent(
        name=name, organization_id=org_id, archimate_element_id=element.id, **kwargs
    )
    db_session.add(comp)
    db_session.flush()
    return comp


def _owns_app(db_session, org_id, comp, user, ownership_type="primary"):
    from app.models.application_owner import ApplicationOwner

    db_session.add(
        ApplicationOwner(
            user_id=user.id,
            application_id=comp.id,
            organization_id=org_id,
            ownership_type=ownership_type,
        )
    )
    db_session.flush()


def _owns_capability(db_session, org_id, unified_capability_id, user):
    from app.models.application_owner import ApplicationOwner

    db_session.add(
        ApplicationOwner(
            user_id=user.id,
            element_type="capability",
            element_id=unified_capability_id,
            organization_id=org_id,
            ownership_type="primary",
        )
    )
    db_session.flush()


def _estate(db_session, org, *, downstream_apps=1, owner_first="Dana"):
    """root app -> service -> interface, root -> N downstream apps (each owned).

    The real root component has id SHARED_ID and its element id is
    ROOT_ELEMENT_ID. A decoy ELEMENT with id SHARED_ID (same org) has its own
    downstream, so reading the approval's application id as an element id
    names the wrong thing.
    """
    root_el = _element(db_session, org.id, "Billing Core", "ApplicationComponent", element_id=ROOT_ELEMENT_ID)
    root = _component(db_session, org.id, root_el, "Billing Core", comp_id=SHARED_ID)
    service = _element(db_session, org.id, "Invoice Service", "ApplicationService")
    interface = _element(db_session, org.id, "Invoice API", "ApplicationInterface")
    _serves(db_session, org.id, root_el, service)
    _serves(db_session, org.id, service, interface)

    names = []
    for i in range(downstream_apps):
        name = f"Reporting App {i}"
        el = _element(db_session, org.id, name, "ApplicationComponent")
        comp = _component(db_session, org.id, el, name)
        owner = _user(db_session, org, f"down{i}", first=owner_first, last=f"Owner{i}")
        _owns_app(db_session, org.id, comp, owner)
        _serves(db_session, org.id, root_el, el)
        names.append(name)

    decoy = _element(db_session, org.id, "Decoy Element", "ApplicationComponent", element_id=SHARED_ID)
    decoy_down = _element(db_session, org.id, "Decoy Downstream", "ApplicationService")
    _serves(db_session, org.id, decoy, decoy_down)
    return {"root": root, "root_el": root_el, "names": names}


def _propose(
    tenant_ctx, org, requester, *, operation_type, entity_type, payload, entity_id=None, summary="A change",
    deliver=True,
):
    """Create a proposal in a request context. Owner email is sent once the
    response has been sent, so ``deliver`` plays that moment (as the server does
    when the response closes); ``deliver=False`` leaves it pending."""
    from flask import current_app

    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    with tenant_ctx(org.id):
        result = AIChatApprovalService(requester.id).create_pending_approval(
            operation_type=operation_type,
            entity_type=entity_type,
            original_command="test",
            operation_payload=payload,
            summary=summary,
            entity_id=entity_id,
            chat_session_id="impact-sess",
        )
        if deliver:
            current_app.process_response(current_app.response_class()).close()
    assert result["success"] is True, result
    return result["approval_id"]


def _impact(tenant_ctx, org, reviewer, approval_id):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    with tenant_ctx(org.id):
        return AIChatApprovalService(reviewer.id).get_approval_impact(approval_id)


def _names(answer, bucket):
    return sorted(item["name"] for item in answer["affected"][bucket])


# ---------------------------------------------------------------------------
# the preview
# ---------------------------------------------------------------------------


def test_application_change_names_services_interfaces_applications_and_owners(
    db_session, make_org, tenant_ctx
):
    org = make_org("prev-app")
    requester = _user(db_session, org, "requester")
    reviewer = _user(db_session, org, "reviewer")
    _estate(db_session, org, downstream_apps=2)
    approval_id = _propose(
        tenant_ctx, org, requester,
        operation_type="update", entity_type="application", entity_id=SHARED_ID,
        payload={"name": "Billing Core v2"}, summary="Rename Billing Core",
    )

    result = _impact(tenant_ctx, org, reviewer, approval_id)

    assert result["success"] is True
    answer = result["impact"]
    assert answer["state"] == "computed"
    assert answer["subjects"] == ["Billing Core"]
    assert _names(answer, "services") == ["Invoice Service"]
    assert _names(answer, "interfaces") == ["Invoice API"]
    assert _names(answer, "applications") == ["Reporting App 0", "Reporting App 1"]
    # Owners come from the one ownership store, with a name rather than an email.
    owners = {
        item["name"]: [o["name"] for o in item["owners"]] for item in answer["affected"]["applications"]
    }
    assert owners["Reporting App 0"] == ["Dana Owner0"]
    assert owners["Reporting App 1"] == ["Dana Owner1"]
    assert "Decoy Downstream" not in str(answer)
    assert answer["truncated"] is False
    assert "Invoice Service" not in answer["message"]  # counts, not a wall of names
    assert "1 service" in answer["message"] and "2 applications" in answer["message"]


def test_owner_without_a_name_is_a_dash_never_an_email(db_session, make_org, tenant_ctx):
    org = make_org("prev-dash")
    requester = _user(db_session, org, "requester")
    reviewer = _user(db_session, org, "reviewer")
    estate = _estate(db_session, org, downstream_apps=1, owner_first="")
    # blank both names on the downstream owner
    from app.models.application_owner import ApplicationOwner
    from app.models.user import User

    owner_row = ApplicationOwner.query.filter(
        ApplicationOwner.organization_id == org.id, ApplicationOwner.application_id != estate["root"].id
    ).first()
    owner_user = db_session.get(User, owner_row.user_id)
    owner_user.first_name = None
    owner_user.last_name = None
    db_session.flush()
    approval_id = _propose(
        tenant_ctx, org, requester,
        operation_type="update", entity_type="application", entity_id=SHARED_ID, payload={"name": "x"},
    )

    answer = _impact(tenant_ctx, org, reviewer, approval_id)["impact"]

    shown = [o["name"] for item in answer["affected"]["applications"] for o in item["owners"]]
    assert shown == ["—"]
    assert "@" not in str(answer)


def test_application_id_is_never_read_as_an_element_id(db_session, make_org, tenant_ctx):
    """The approval names an ApplicationComponent id; a decoy ELEMENT with the
    same number has a different downstream and must never be walked."""
    org = make_org("prev-decoy")
    requester = _user(db_session, org, "requester")
    reviewer = _user(db_session, org, "reviewer")
    _estate(db_session, org, downstream_apps=1)
    approval_id = _propose(
        tenant_ctx, org, requester,
        operation_type="delete", entity_type="application", entity_id=SHARED_ID, payload={},
    )

    answer = _impact(tenant_ctx, org, reviewer, approval_id)["impact"]

    assert answer["subjects"] == ["Billing Core"]
    assert "Decoy Downstream" not in str(answer)
    assert _names(answer, "services") == ["Invoice Service"]


def _capability_estate(db_session, org):
    """BusinessCapability CAP_SHARED_ID projected to a UnifiedCapability whose id
    differs; a decoy UnifiedCapability has id CAP_SHARED_ID and its own downstream."""
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_capability import UnifiedCapability

    business = BusinessCapability(
        id=CAP_SHARED_ID, name="Order to Cash", level=1, business_domain="Finance", organization_id=org.id
    )
    db_session.add(business)
    db_session.flush()
    cap_el = _element(db_session, org.id, "Order to Cash", "Capability")
    # Where the write-time projection is installed it has already made the
    # UnifiedCapability for this BusinessCapability; otherwise create it.
    real = UnifiedCapability.query.filter(
        UnifiedCapability.source_table == "business_capability",
        UnifiedCapability.source_id == str(CAP_SHARED_ID),
        UnifiedCapability.source_org_id == org.id,
    ).first()
    if real is None:
        real = UnifiedCapability(
            name="Order to Cash",
            organization_id=org.id,
            source_table="business_capability",
            source_id=str(CAP_SHARED_ID),
            source_org_id=org.id,
        )
        db_session.add(real)
    real.name = "Order to Cash"
    real.archimate_element_id = cap_el.id
    db_session.flush()
    realised = _element(db_session, org.id, "Billing Core", "ApplicationComponent")
    db_session.flush()
    _serves(db_session, org.id, cap_el, realised)

    decoy_el = _element(db_session, org.id, "Decoy Capability Element", "Capability")
    decoy = UnifiedCapability(
        id=CAP_SHARED_ID, name="Decoy Capability", organization_id=org.id, archimate_element_id=decoy_el.id
    )
    db_session.add(decoy)
    decoy_down = _element(db_session, org.id, "Decoy Capability Downstream", "ApplicationService")
    db_session.flush()
    _serves(db_session, org.id, decoy_el, decoy_down)
    return {"real": real, "decoy": decoy}


def test_capability_change_is_found_by_provenance_not_by_unified_id(db_session, make_org, tenant_ctx):
    org = make_org("prev-cap")
    requester = _user(db_session, org, "requester")
    reviewer = _user(db_session, org, "reviewer")
    caps = _capability_estate(db_session, org)
    assert caps["real"].id != CAP_SHARED_ID
    approval_id = _propose(
        tenant_ctx, org, requester,
        operation_type="update", entity_type="capability", entity_id=CAP_SHARED_ID, payload={"name": "OTC"},
    )

    answer = _impact(tenant_ctx, org, reviewer, approval_id)["impact"]

    assert answer["state"] == "computed"
    assert answer["subjects"] == ["Order to Cash"]
    assert _names(answer, "applications") == ["Billing Core"]
    assert "Decoy" not in str(answer)


def test_link_change_walks_both_ends(db_session, make_org, tenant_ctx):
    org = make_org("prev-link")
    requester = _user(db_session, org, "requester")
    reviewer = _user(db_session, org, "reviewer")
    _estate(db_session, org, downstream_apps=1)
    _capability_estate(db_session, org)
    approval_id = _propose(
        tenant_ctx, org, requester,
        operation_type="link", entity_type="application_capability_mapping",
        payload={"application_id": SHARED_ID, "capability_id": CAP_SHARED_ID},
    )

    answer = _impact(tenant_ctx, org, reviewer, approval_id)["impact"]

    assert sorted(answer["subjects"]) == ["Billing Core", "Order to Cash"]
    assert "Invoice Service" in _names(answer, "services")
    assert "Decoy" not in str(answer)


# ---------------------------------------------------------------------------
# honest empty states
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "operation_type, entity_type, payload, expected",
    [
        ("create", "capability", {"name": "New"}, "creates something new"),
        ("update", "vendor", {"name": "V"}, "not linked to the architecture model"),
        ("create", "capability_mapping", {"unified_capability_id": "x"}, "creates something new"),
    ],
)
def test_changes_outside_the_model_say_so(
    db_session, make_org, tenant_ctx, operation_type, entity_type, payload, expected
):
    org = make_org("prev-none")
    requester = _user(db_session, org, "requester")
    reviewer = _user(db_session, org, "reviewer")
    approval_id = _propose(
        tenant_ctx, org, requester,
        operation_type=operation_type, entity_type=entity_type, entity_id=1, payload=payload,
    )

    answer = _impact(tenant_ctx, org, reviewer, approval_id)["impact"]

    assert answer["state"] == "not_in_model"
    assert expected in answer["message"]
    assert answer["subjects"] == []


def test_item_nothing_depends_on_says_so(db_session, make_org, tenant_ctx):
    org = make_org("prev-lonely")
    requester = _user(db_session, org, "requester")
    reviewer = _user(db_session, org, "reviewer")
    el = _element(db_session, org.id, "Lonely App", "ApplicationComponent")
    comp = _component(db_session, org.id, el, "Lonely App")
    approval_id = _propose(
        tenant_ctx, org, requester,
        operation_type="update", entity_type="application", entity_id=comp.id, payload={"name": "x"},
    )

    answer = _impact(tenant_ctx, org, reviewer, approval_id)["impact"]

    assert answer["state"] == "no_dependents"
    assert "Nothing in the architecture model depends on Lonely App" in answer["message"]
    assert all(answer["affected"][k] == [] for k in answer["affected"])


def test_a_long_answer_is_cut_and_says_so(db_session, make_org, tenant_ctx):
    from app.services.architecture_change_impact_service import ArchitectureChangeImpactService

    org = make_org("prev-long")
    requester = _user(db_session, org, "requester")
    reviewer = _user(db_session, org, "reviewer")
    root_el = _element(db_session, org.id, "Hub", "ApplicationComponent")
    comp = _component(db_session, org.id, root_el, "Hub")
    for i in range(ArchitectureChangeImpactService.APPROVAL_PREVIEW_LIMIT + 5):
        _serves(db_session, org.id, root_el, _element(db_session, org.id, f"Spoke {i}", "ApplicationService"))
    approval_id = _propose(
        tenant_ctx, org, requester,
        operation_type="update", entity_type="application", entity_id=comp.id, payload={"name": "x"},
    )

    answer = _impact(tenant_ctx, org, reviewer, approval_id)["impact"]

    assert answer["truncated"] is True
    assert answer["shown"] == ArchitectureChangeImpactService.APPROVAL_PREVIEW_LIMIT
    assert "Showing the first 25" in answer["message"]


# ---------------------------------------------------------------------------
# tenancy, authorisation, unset organisation
# ---------------------------------------------------------------------------


def test_foreign_organisation_cannot_preview_and_never_leaks_into_the_answer(
    db_session, make_org, tenant_ctx
):
    org_a, org_b = make_org("prev-iso-a"), make_org("prev-iso-b")
    requester_a = _user(db_session, org_a, "reqa")
    reviewer_a = _user(db_session, org_a, "revwa")
    reviewer_b = _user(db_session, org_b, "revwb")
    estate = _estate(db_session, org_a, downstream_apps=1)
    # org B has an element hanging off org A's root: it must never be walked into.
    foreign = _element(db_session, org_b.id, "Org B Secret", "ApplicationService")
    _serves(db_session, org_b.id, estate["root_el"], foreign)
    approval_id = _propose(
        tenant_ctx, org_a, requester_a,
        operation_type="update", entity_type="application", entity_id=SHARED_ID, payload={"name": "x"},
    )

    foreign_try = _impact(tenant_ctx, org_b, reviewer_b, approval_id)
    own = _impact(tenant_ctx, org_a, reviewer_a, approval_id)

    assert foreign_try["success"] is False and foreign_try["code"] == "NOT_FOUND"
    assert own["success"] is True
    assert "Org B Secret" not in str(own)
    assert own["impact"]["state"] == "computed"


def test_unset_organisation_reads_nothing_and_refuses(db_session, make_org, app, tenant_ctx):
    from flask import g

    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
    from app.services.architecture_change_impact_service import (
        ArchitectureChangeImpactService,
        resolve_approval_targets,
    )

    org = make_org("prev-unset")
    requester = _user(db_session, org, "requester")
    reviewer = _user(db_session, org, "reviewer")
    _estate(db_session, org, downstream_apps=1)
    approval_id = _propose(
        tenant_ctx, org, requester,
        operation_type="update", entity_type="application", entity_id=SHARED_ID, payload={"name": "x"},
    )

    with app.test_request_context("/"):
        g.current_org_id = None
        refused = AIChatApprovalService(reviewer.id).get_approval_impact(approval_id)
        decided = AIChatApprovalService(reviewer.id).get_recent_decisions()
        assert resolve_approval_targets("update", "application", SHARED_ID, {}, None) == []
    with tenant_ctx(org.id):
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval

        approval = AIChatCRUDApproval.query.filter_by(id=approval_id, organization_id=org.id).one()
        unavailable = ArchitectureChangeImpactService().assess_approval_impact(approval, None)

    assert refused["success"] is False and refused["code"] == "NOT_FOUND"
    assert decided["success"] is False and decided["code"] == "NOT_FOUND"
    assert unavailable["state"] == "unavailable"
    assert unavailable["affected"]["services"] == []


def test_impact_answer_is_unavailable_when_the_active_organisation_differs(db_session, make_org, tenant_ctx):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    from app.services.architecture_change_impact_service import ArchitectureChangeImpactService

    org_a, org_b = make_org("prev-active-a"), make_org("prev-active-b")
    requester = _user(db_session, org_a, "requester")
    _estate(db_session, org_a, downstream_apps=1)
    approval_id = _propose(
        tenant_ctx, org_a, requester,
        operation_type="update", entity_type="application", entity_id=SHARED_ID, payload={"name": "x"},
    )
    with tenant_ctx(org_a.id):
        approval = AIChatCRUDApproval.query.filter_by(id=approval_id, organization_id=org_a.id).one()
    with tenant_ctx(org_b.id):
        answer = ArchitectureChangeImpactService().assess_approval_impact(approval, org_a.id)

    assert answer["state"] == "unavailable"
    assert answer["affected"]["services"] == [] and answer["subjects"] == []


def test_routes_enforce_the_reviewer_permission_and_organisation(app, db_session, make_org, tenant_ctx, login_as):
    org_a, org_b = make_org("prev-route-a"), make_org("prev-route-b")
    requester = _user(db_session, org_a, "requester")
    reviewer = _user(db_session, org_a, "reviewer")
    viewer = _user(db_session, org_a, "viewer", can_approve=False)
    outsider = _user(db_session, org_b, "outsider")
    _estate(db_session, org_a, downstream_apps=1)
    approval_id = _propose(
        tenant_ctx, org_a, requester,
        operation_type="update", entity_type="application", entity_id=SHARED_ID, payload={"name": "x"},
    )
    url = f"/ai-chat/approvals/{approval_id}/impact"

    anonymous = app.test_client().get(url)
    assert anonymous.status_code in (302, 401)

    client = app.test_client()
    login_as(client, viewer)
    assert client.get(url).status_code == 403
    assert client.get("/ai-chat/approvals/decided").status_code == 403

    login_as(client, outsider)
    assert client.get(url).status_code == 404

    login_as(client, reviewer)
    ok = client.get(url)
    assert ok.status_code == 200
    body = ok.get_json()
    assert body["success"] is True and body["impact"]["state"] == "computed"
    assert client.get("/ai-chat/approvals/999999999/impact").status_code == 404


# ---------------------------------------------------------------------------
# query counts
# ---------------------------------------------------------------------------


def _count_statements(fn):
    from app import db

    engine = db.session.get_bind()
    counter = {"n": 0}

    def _cnt(*_a, **_k):
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _cnt)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", _cnt)
    return counter["n"]


def test_preview_query_count_does_not_grow_with_the_rows(db_session, make_org, tenant_ctx):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    from app.services.architecture_change_impact_service import ArchitectureChangeImpactService

    counts = []
    for label, apps in (("few", 2), ("many", 14)):
        org = make_org(f"prev-count-{label}")
        requester = _user(db_session, org, "requester")
        # The same root ids are used in both organisations, so build them one at
        # a time with the ids released by the first organisation's rollback-free
        # flush: use fresh ids instead of the colliding ones.
        root_el = _element(db_session, org.id, "Root", "ApplicationComponent")
        comp = _component(db_session, org.id, root_el, "Root")
        for i in range(apps):
            el = _element(db_session, org.id, f"App {i}", "ApplicationComponent")
            down = _component(db_session, org.id, el, f"App {i}")
            _owns_app(db_session, org.id, down, _user(db_session, org, f"o{label}{i}"))
            _serves(db_session, org.id, root_el, el)
        approval_id = _propose(
            tenant_ctx, org, requester,
            operation_type="update", entity_type="application", entity_id=comp.id, payload={"name": "x"},
        )
        with tenant_ctx(org.id):
            approval = AIChatCRUDApproval.query.filter_by(id=approval_id, organization_id=org.id).one()
            service = ArchitectureChangeImpactService()
            service.assess_approval_impact(approval, org.id)  # warm
            counts.append(_count_statements(lambda: service.assess_approval_impact(approval, org.id)))

    assert counts[0] == counts[1], f"query count grew with the rows: {counts}"


def test_decided_list_query_count_does_not_grow_with_the_rows(db_session, make_org, tenant_ctx):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    counts = []
    for label, decided in (("few", 1), ("many", 9)):
        org = make_org(f"prev-dcount-{label}")
        requester = _user(db_session, org, "requester")
        reviewer = _user(db_session, org, "reviewer")
        for i in range(decided):
            approval_id = _propose(
                tenant_ctx, org, requester,
                operation_type="create", entity_type="capability", payload={"name": f"c{i}"},
                summary=f"Create c{i}",
            )
            with tenant_ctx(org.id):
                assert AIChatApprovalService(reviewer.id).reject_approval(approval_id, "no")["success"]
        with tenant_ctx(org.id):
            service = AIChatApprovalService(reviewer.id)
            service.get_recent_decisions()  # warm
            counts.append(_count_statements(service.get_recent_decisions))

    assert counts[0] == counts[1], f"query count grew with the rows: {counts}"


# ---------------------------------------------------------------------------
# the decided list keeps a rejection's reason
# ---------------------------------------------------------------------------


def test_rejection_reason_is_recorded_and_still_shown_after_reload(app, db_session, make_org, tenant_ctx, login_as):
    org_a, org_b = make_org("prev-decided-a"), make_org("prev-decided-b")
    requester = _user(db_session, org_a, "requester")
    reviewer = _user(db_session, org_a, "reviewer")
    outsider = _user(db_session, org_b, "outsider")
    approval_id = _propose(
        tenant_ctx, org_a, requester,
        operation_type="create", entity_type="capability", payload={"name": "n"}, summary="Create capability n",
    )
    client = app.test_client()

    login_as(client, reviewer)
    assert client.get("/ai-chat/approvals/decided").get_json()["decisions"] == []
    rejected = client.post(f"/ai-chat/approvals/{approval_id}/reject", json={"reason": "We already have this"})
    assert rejected.status_code == 200

    # a fresh request, as after a reload
    login_as(client, reviewer)
    decisions = client.get("/ai-chat/approvals/decided").get_json()["decisions"]
    assert [(d["id"], d["status"], d["rejected_reason"], d["summary"]) for d in decisions] == [
        (approval_id, "rejected", "We already have this", "Create capability n")
    ]
    assert client.get("/ai-chat/approvals/queue").get_json()["approvals"] == []

    login_as(client, outsider)
    assert client.get("/ai-chat/approvals/decided").get_json()["decisions"] == []


def test_inbox_page_has_the_impact_and_decided_regions(app, db_session, make_org, tenant_ctx, login_as):
    """The inbox is rendered on the server: each proposal offers "What does this
    affect?", which reopens the page with that proposal's impact worked out, and
    the Recently decided region is always there (empty until something is decided).
    It used to be an empty shell filled by a script the layout never output."""
    org = make_org("prev-page")
    requester = _user(db_session, org, "requester")
    reviewer = _user(db_session, org, "reviewer")
    approval_id = _propose(
        tenant_ctx, org, requester,
        operation_type="create", entity_type="capability", payload={"name": "n"}, summary="Create capability n",
    )
    client = app.test_client()
    login_as(client, reviewer)

    html = client.get("/ai-chat/approvals/inbox").get_data(as_text=True)

    assert 'data-testid="approval-card-%s"' % approval_id in html
    assert 'data-testid="approval-impact-toggle-%s"' % approval_id in html
    assert "/ai-chat/approvals/inbox?approval=%s#approval-%s" % (approval_id, approval_id) in html
    assert 'data-testid="approval-decided"' in html
    assert 'data-testid="approval-decided-empty"' in html

    # Opening the toggle renders that proposal's impact region.
    login_as(client, reviewer)
    html = client.get("/ai-chat/approvals/inbox?approval=%s" % approval_id).get_data(as_text=True)
    assert 'data-testid="approval-impact-%s"' % approval_id in html


# ---------------------------------------------------------------------------
# notification on a new proposal
# ---------------------------------------------------------------------------


def _bell(org, user):
    from app.models.models import Notification

    return (
        Notification.query.filter_by(user_id=user.id, organization_id=org.id, event_type="approval_requested")
        .order_by(Notification.id)
        .all()
    )


def test_owners_are_told_in_app_and_by_email_and_nobody_else(db_session, make_org, tenant_ctx):
    org_a, org_b = make_org("prev-note-a"), make_org("prev-note-b")
    requester = _user(db_session, org_a, "requester")
    owner = _user(db_session, org_a, "owner")
    bystander = _user(db_session, org_a, "bystander")
    foreign_owner = _user(db_session, org_b, "foreignowner")
    estate = _estate(db_session, org_a, downstream_apps=1)
    _owns_app(db_session, org_a.id, estate["root"], owner)
    _owns_app(db_session, org_a.id, estate["root"], requester, "backup")
    # org B owner of an application with the same number as org A's root
    from app.models.application_portfolio import ApplicationComponent

    foreign_el = _element(db_session, org_b.id, "Foreign App", "ApplicationComponent")
    foreign_comp = ApplicationComponent(name="Foreign App", organization_id=org_b.id, archimate_element_id=foreign_el.id)
    db_session.add(foreign_comp)
    db_session.flush()
    _owns_app(db_session, org_b.id, foreign_comp, foreign_owner)

    with patch("app._bootstrap._digest_emails._safe_send_email", return_value=True) as send:
        approval_id = _propose(
            tenant_ctx, org_a, requester,
            operation_type="update", entity_type="application", entity_id=SHARED_ID,
            payload={"name": "x"}, summary="Rename Billing Core <b>",
        )

    with tenant_ctx(org_a.id):
        told = _bell(org_a, owner)
        assert len(told) == 1
        note = told[0]
        assert note.entity_type == "approval" and note.entity_id == approval_id
        assert note.url == f"/ai-chat/approvals/inbox?approval={approval_id}"
        assert "Rename Billing Core" in note.message
        assert note.email_sent_at is not None
        assert _bell(org_a, requester) == []
        assert _bell(org_a, bystander) == []
    with tenant_ctx(org_b.id):
        assert _bell(org_b, foreign_owner) == []

    recipients = [call.args[2] for call in send.call_args_list]
    assert [owner.email] in recipients
    assert [requester.email] not in recipients
    assert [foreign_owner.email] not in recipients
    html = send.call_args_list[0].args[3]
    assert "&lt;b&gt;" in html and "<b>" not in html  # the summary is escaped
    # The call to action is an absolute link (a relative one goes nowhere in a mail client).
    href = html.split('href="', 1)[1].split('"', 1)[0]
    assert href.startswith("http"), href
    assert href.endswith(f"/ai-chat/approvals/inbox?approval={approval_id}")


def test_owner_email_waits_for_the_response_and_records_delivery(db_session, make_org, tenant_ctx, app):
    from flask import current_app

    org = make_org("prev-note-defer")
    requester = _user(db_session, org, "requester")
    owner = _user(db_session, org, "owner")
    estate = _estate(db_session, org, downstream_apps=0)
    _owns_app(db_session, org.id, estate["root"], owner)

    with patch("app._bootstrap._digest_emails._safe_send_email", return_value=True) as send:
        with tenant_ctx(org.id):
            from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

            result = AIChatApprovalService(requester.id).create_pending_approval(
                operation_type="update", entity_type="application", entity_id=SHARED_ID,
                original_command="t", operation_payload={"name": "x"}, summary="Deferred",
            )
            assert result["success"] is True
            # The proposal and the bell exist; the mail server has not been touched.
            assert send.call_count == 0
            assert len(_bell(org, owner)) == 1
            assert _bell(org, owner)[0].email_sent_at is None
            current_app.process_response(current_app.response_class()).close()
        assert send.call_count == 1

    with tenant_ctx(org.id):
        assert _bell(org, owner)[0].email_sent_at is not None


def test_without_a_base_url_or_request_the_email_has_no_relative_link(db_session, make_org, app):
    """Outside a request with no configured base URL the link is left out, never relative."""
    from flask import g

    from app.models.ai_chat_crud_approval import AIChatCRUDApproval  # noqa: F401
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org = make_org("prev-note-nolink")
    requester = _user(db_session, org, "requester")
    owner = _user(db_session, org, "owner")
    estate = _estate(db_session, org, downstream_apps=0)
    _owns_app(db_session, org.id, estate["root"], owner)
    app.config["PUBLIC_BASE_URL"] = ""
    g.current_org_id = org.id  # the test's own application context: no request is active
    with patch("app._bootstrap._digest_emails._safe_send_email", return_value=True) as send:
        AIChatApprovalService(requester.id).create_pending_approval(
            operation_type="update", entity_type="application", entity_id=SHARED_ID,
            original_command="t", operation_payload={"name": "x"}, summary="No link",
        )
    assert send.call_count == 1
    assert "href" not in send.call_args.args[3]


def test_capability_owner_is_found_by_the_unified_id_not_the_source_id(db_session, make_org, tenant_ctx):
    org = make_org("prev-note-cap")
    requester = _user(db_session, org, "requester")
    owner = _user(db_session, org, "capowner")
    decoy_owner = _user(db_session, org, "decoyowner")
    caps = _capability_estate(db_session, org)
    _owns_capability(db_session, org.id, caps["real"].id, owner)
    _owns_capability(db_session, org.id, CAP_SHARED_ID, decoy_owner)  # the decoy capability's owner

    with patch("app._bootstrap._digest_emails._safe_send_email", return_value=False):
        approval_id = _propose(
            tenant_ctx, org, requester,
            operation_type="update", entity_type="capability", entity_id=CAP_SHARED_ID, payload={"name": "OTC"},
        )

    with tenant_ctx(org.id):
        assert [n.entity_id for n in _bell(org, owner)] == [approval_id]
        assert _bell(org, decoy_owner) == []
        # mail not sent (no SMTP): the bell still has it and says it was not emailed
        assert _bell(org, owner)[0].email_sent_at is None


def test_a_notification_failure_never_fails_the_proposal(db_session, make_org, tenant_ctx):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    org = make_org("prev-note-fail")
    requester = _user(db_session, org, "requester")
    owner = _user(db_session, org, "owner")
    estate = _estate(db_session, org, downstream_apps=0)
    _owns_app(db_session, org.id, estate["root"], owner)

    with patch(
        "app.services.notification_service.NotificationService.create", side_effect=RuntimeError("bell down")
    ) as bell:
        approval_id = _propose(
            tenant_ctx, org, requester,
            operation_type="update", entity_type="application", entity_id=SHARED_ID, payload={"name": "x"},
        )

    assert bell.called  # the owner was found and the bell was tried
    with tenant_ctx(org.id):
        assert AIChatCRUDApproval.query.filter_by(id=approval_id, organization_id=org.id).count() == 1
        assert _bell(org, owner) == []


def test_creating_something_new_notifies_nobody(db_session, make_org, tenant_ctx):
    org = make_org("prev-note-new")
    requester = _user(db_session, org, "requester")
    owner = _user(db_session, org, "owner")
    estate = _estate(db_session, org, downstream_apps=0)
    _owns_app(db_session, org.id, estate["root"], owner)

    _propose(tenant_ctx, org, requester, operation_type="create", entity_type="capability", payload={"name": "n"})

    with tenant_ctx(org.id):
        assert _bell(org, owner) == []


# ---------------------------------------------------------------------------
# My work
# ---------------------------------------------------------------------------


def test_my_work_carries_the_inbox_for_application_manager_and_business_owner():
    """The inbox is the single queue and is already in the My work zone of every
    role that can approve; this pins it, with its permission guard, for the
    application manager and the non-technical (business) owner."""
    from app.models.user import ROLE_APPLICATION_MANAGER, ROLE_NON_TECHNICAL_OWNER
    from app.utils import role_access

    for role in (ROLE_APPLICATION_MANAGER, ROLE_NON_TECHNICAL_OWNER):
        links = role_access._MY_WORK_LINKS[role]
        inbox = [link for link in links if link["endpoint"] == "unified_ai_chat.approval_inbox"]
        assert len(inbox) == 1, role
        assert inbox[0]["requires"] == "general"
