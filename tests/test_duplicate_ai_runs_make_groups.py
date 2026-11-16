"""The AI duplicate runs group near-duplicate applications and nothing else.

Both runs score a pair by the business capabilities the two applications are
mapped to. Two applications in one business domain with different capabilities
and unrelated names are not duplicates; two with the same capabilities are.
Two organisations run in turn, and each is scored on its own capabilities.
"""

from __future__ import annotations

import pytest

from app import db

pytestmark = pytest.mark.usefixtures("db_session")

STRATEGIES = ("ai_enhanced", "business_aware")


def _seed_app(db_session, org, name, description):
    from app.models.application_portfolio import ApplicationComponent

    component = ApplicationComponent(
        name=name,
        description=description,
        component_type="application",
        technology_stack="java, postgres",
        lifecycle_status="active",
        business_domain="Finance",
        organization_id=org.id,
    )
    db_session.add(component)
    db_session.flush()
    return component


def _map(db_session, org, component, capability):
    from app.models.application_capability import ApplicationCapabilityMapping

    db_session.add(
        ApplicationCapabilityMapping(
            organization_id=org.id,
            application_component_id=component.id,
            business_capability_id=capability.id,
        )
    )
    db_session.flush()


def _capability(db_session, org, name):
    from app.models.business_capabilities import BusinessCapability

    capability = BusinessCapability(name=name, organization_id=org.id)
    db_session.add(capability)
    db_session.flush()
    return capability


def _user(db_session, org, role, tag):
    from app.models.user import Role, User

    user = User(
        email=f"dupai-{tag}-{org.id}@example.com",
        first_name="Dup",
        last_name="Ai",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
    )
    if role == "platform_admin":
        user.is_platform_admin = True
        user.role = Role.query.filter_by(name="Administrator").first() or user.role
    db_session.add(user)
    db_session.flush()
    return user


class _Caller:
    """A signed-in client that signs in again before each request, so the
    tenant and user cached on ``g`` by the previous caller never leak in."""

    def __init__(self, client, user, login_as):
        self._client, self._user, self._login_as = client, user, login_as

    def _fresh(self):
        self._login_as(self._client, self._user)
        return self._client

    def get(self, *args, **kwargs):
        return self._fresh().get(*args, **kwargs)

    def post(self, *args, **kwargs):
        return self._fresh().post(*args, **kwargs)


@pytest.fixture
def world(app, db_session, make_org, login_as):
    org = make_org("dupai")
    other = make_org("dupai-other")

    invoicing = _capability(db_session, org, "Invoicing")
    payroll = _capability(db_session, org, "Payroll")
    stock = _capability(db_session, org, "Stock Control")
    _capability(db_session, other, "Invoicing")

    desc = "Customer billing and invoicing platform"
    first = _seed_app(db_session, org, "Billing Portal", desc)
    second = _seed_app(db_session, org, "Billing Portal Service", desc)
    _map(db_session, org, first, invoicing)
    _map(db_session, org, second, invoicing)
    # Same business domain, different capabilities, unrelated names.
    payroll_app = _seed_app(db_session, org, "Payroll Engine", "Runs salary cycles")
    stock_app = _seed_app(db_session, org, "Inventory Tracker", "Counts warehouse stock")
    _map(db_session, org, payroll_app, payroll)
    _map(db_session, org, stock_app, stock)

    # The other organisation holds look-alike names with no mapped capability.
    _seed_app(db_session, other, "Billing Portal", desc)
    _seed_app(db_session, other, "Billing Portal Service", desc)

    admin = _user(db_session, org, "platform_admin", "admin")
    member = _user(db_session, org, "enterprise_architect", "member")
    other_admin = _user(db_session, other, "platform_admin", "other")
    app.config["WTF_CSRF_ENABLED"] = False

    def client_for(user):
        raw = app.test_client()
        return _Caller(raw, user, login_as)

    return {
        "org": org,
        "other": other,
        "admin": client_for(admin),
        "member": client_for(member),
        "other_admin": client_for(other_admin),
        "near_pair": {first.id, second.id},
        "unrelated_pair": {payroll_app.id, stock_app.id},
    }


def _run(client, strategy):
    response = client.post(
        "/duplicate-detection/ai/api/detect",
        json={"strategy": strategy, "threshold": 0.65},
    )
    return response, response.get_json() or {}


def _member_sets(db_session, run_id):
    from app.models.unified_duplicate_detection import UnifiedDuplicateGroup

    groups = UnifiedDuplicateGroup.query.filter_by(detection_run_id=run_id).all()
    return [{m.id for m in g.applications} for g in groups]


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_near_duplicates_are_grouped_and_same_domain_strangers_are_not(
    world, db_session, strategy
):
    response, body = _run(world["admin"], strategy)
    assert response.status_code == 200 and body.get("success") is True, body

    sets = _member_sets(db_session, body["run_id"])
    assert world["near_pair"] in sets, f"{strategy} did not group the near-duplicates"
    assert world["unrelated_pair"] not in sets, (
        f"{strategy} grouped two applications that only share a domain"
    )
    assert all(m <= (world["near_pair"] | world["unrelated_pair"]) for m in sets)


def test_member_who_is_not_a_platform_admin_cannot_start_a_run(world, db_session):
    from app.models.unified_duplicate_detection import UnifiedDetectionRun

    before = UnifiedDetectionRun.query.count()
    response, _ = _run(world["member"], "ai_enhanced")
    assert response.status_code == 403
    assert UnifiedDetectionRun.query.count() == before


def test_insights_page_shows_the_runs_pairs(world, db_session):
    _, body = _run(world["admin"], "ai_enhanced")
    page = world["admin"].get(f"/duplicate-detection/ai/insights/{body['run_id']}")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "No AI insights available" not in html
    assert "Billing Portal" in html


def test_dashboard_last_detection_is_the_ai_run(world, db_session):
    _, body = _run(world["admin"], "business_aware")
    stats = world["admin"].get("/duplicate-detection/simple/api/statistics").get_json()
    assert stats["latest_run"] is not None, "Last Detection reads Never after an AI run"
    assert stats["latest_run"]["id"] == body["run_id"]
    assert stats["total_groups"] >= 1


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_second_organisation_is_scored_on_its_own_capabilities(
    world, db_session, strategy
):
    # The first organisation runs and maps "Invoicing"; the second has no
    # mapped capability, so its look-alike pair must not be grouped on the
    # strength of the first organisation's.
    _run(world["admin"], strategy)
    response, body = _run(world["other_admin"], strategy)
    assert response.status_code == 200 and body.get("success") is True, body
    sets = _member_sets(db_session, body["run_id"])
    from app.models.application_portfolio import ApplicationComponent

    other_ids = {
        row[0]
        for row in db_session.execute(
            db.select(ApplicationComponent.id).where(
                ApplicationComponent.organization_id == world["other"].id
            )
        )
    }
    assert other_ids
    assert all(m <= other_ids for m in sets), (sets, other_ids, world["near_pair"])
    if strategy == "business_aware":
        assert sets == []
