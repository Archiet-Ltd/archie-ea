"""The capability map lists the applications its counts say are mapped, for the signed-in organisation only."""

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _owner(db_session, org):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="map.%s@example.test" % uuid.uuid4().hex[:8], first_name="Map", last_name="Owner",
        organization_id=org.id, enterprise_role="solution_architect", confirmed=True,
    )
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "Sup3r-Secret!pass"
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "org_admin", granted_by_id=user.id)
    db_session.flush()
    return user


def _seed(db_session, org, code, app_names):
    from app.models.application_layer import ApplicationComponent
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(
        name="Capability %s" % code, code=code, level=1, capability_type="strategic",
        organization_id=org.id, scope="tenant",
    )
    db_session.add(cap)
    db_session.flush()
    for name in app_names:
        comp = ApplicationComponent(name=name, lifecycle_status="active", organization_id=org.id)
        db_session.add(comp)
        db_session.flush()
        db_session.add(UnifiedApplicationCapabilityMapping(
            application_component_id=comp.id, unified_capability_id=cap.id, support_level="full",
            coverage_percentage=80,
        ))
    db_session.flush()
    return cap


def _rows(client, login_as, user):
    login_as(client, user)
    reply = client.get("/capability-map/api/capabilities/hierarchy")
    assert reply.status_code == 200, reply.get_data(as_text=True)
    return [c for d in reply.get_json()["domains"] for c in d["capabilities"]]


def test_each_capability_lists_the_applications_its_count_says(client, login_as, db_session, make_org, caplog):
    org = make_org("capmap-a")
    owner = _owner(db_session, org)
    _seed(db_session, org, "SHARED-1", ["CRM System", "Marketing Hub"])
    _seed(db_session, org, "EMPTY-1", [])
    with caplog.at_level("ERROR"):
        rows = {c["code"]: c for c in _rows(client, login_as, owner)}
    mapped = rows["SHARED-1"]
    assert mapped["applicationCoverage"] == 2
    assert sorted(a["name"] for a in mapped["applications"]) == ["CRM System", "Marketing Hub"]
    assert len(mapped["applications"]) == mapped["applicationCoverage"]
    for entry in mapped["applications"]:
        assert client.get(entry["url"]).status_code == 200, entry["url"]
    assert rows["EMPTY-1"]["applications"] == [] and rows["EMPTY-1"]["applicationCoverage"] == 0
    assert "_api_hierarchy_context" not in caplog.text


def test_another_organisation_never_lists_these_applications(client, login_as, db_session, make_org):
    org_a, org_b = make_org("capmap-a"), make_org("capmap-b")
    owner_a, owner_b = _owner(db_session, org_a), _owner(db_session, org_b)
    _seed(db_session, org_a, "SAME-CODE", ["Only A App"])
    _seed(db_session, org_b, "SAME-CODE", ["Only B App"])
    a = {c["code"]: c for c in _rows(client, login_as, owner_a)}["SAME-CODE"]
    b = {c["code"]: c for c in _rows(client, login_as, owner_b)}["SAME-CODE"]
    assert [x["name"] for x in a["applications"]] == ["Only A App"]
    assert [x["name"] for x in b["applications"]] == ["Only B App"]
    assert a["applicationCoverage"] == b["applicationCoverage"] == 1


def test_coverage_equals_the_listed_applications_with_a_cross_organisation_mapping(client, login_as, db_session, make_org):
    """A legacy mapping from this organisation's capability to another organisation's application is
    neither counted nor revealed: coverage is the number of applications listed."""
    from app.models.application_layer import ApplicationComponent
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping

    org_a, org_b = make_org("capmap-a"), make_org("capmap-b")
    owner_a = _owner(db_session, org_a)
    cap = _seed(db_session, org_a, "CROSS-1", ["Own App One", "Own App Two"])
    foreign = ApplicationComponent(name="Foreign Secret App", lifecycle_status="active", organization_id=org_b.id)
    db_session.add(foreign)
    db_session.flush()
    db_session.add(UnifiedApplicationCapabilityMapping(
        application_component_id=foreign.id, unified_capability_id=cap.id, support_level="full",
        coverage_percentage=50,
    ))
    db_session.flush()
    db_session.expire_all()
    row = {c["code"]: c for c in _rows(client, login_as, owner_a)}["CROSS-1"]
    assert sorted(a["name"] for a in row["applications"]) == ["Own App One", "Own App Two"]
    assert row["applicationCoverage"] == row["applicationCount"] == len(row["applications"]) == 2
    assert "Foreign Secret App" not in str(row)


def test_a_missing_application_page_endpoint_only_omits_the_links(client, login_as, db_session, make_org, monkeypatch):
    org = make_org("capmap-a")
    owner = _owner(db_session, org)
    _seed(db_session, org, "LINK-1", ["Linked App"])
    monkeypatch.delitem(client.application.view_functions, "unified_applications.application_detail")
    row = {c["code"]: c for c in _rows(client, login_as, owner)}["LINK-1"]
    assert [a["name"] for a in row["applications"]] == ["Linked App"]
    assert row["applications"][0]["url"] is None
    assert row["applicationCoverage"] == 1


def test_a_failed_load_is_not_cached(client, login_as, db_session, make_org, monkeypatch):
    """With the cache on, one 500 must not keep the map failing: the next request recomputes."""
    from app.extensions import cache as cache_module
    from app.modules.capabilities.services.capability_health_service import CapabilityHealthService

    store = {}
    monkeypatch.setattr(cache_module.cache_manager, "get", lambda key: store.get(key))
    monkeypatch.setattr(cache_module.cache_manager, "set", lambda key, value, ttl=None: store.__setitem__(key, value))

    org = make_org("capmap-a")
    owner = _owner(db_session, org)
    _seed(db_session, org, "CACHE-1", [])
    real = CapabilityHealthService.get_capability_health_metrics
    calls = {"n": 0}

    def flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient failure")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(CapabilityHealthService, "get_capability_health_metrics", flaky)
    login_as(client, owner)
    first = client.get("/capability-map/api/capabilities/hierarchy")
    assert first.status_code == 500
    assert "transient failure" not in first.get_data(as_text=True)
    second = client.get("/capability-map/api/capabilities/hierarchy")
    assert second.status_code == 200, second.get_data(as_text=True)
    assert calls["n"] >= 2
    third_calls = calls["n"]
    assert client.get("/capability-map/api/capabilities/hierarchy").status_code == 200
    assert calls["n"] == third_calls, "a successful load should be cached"
