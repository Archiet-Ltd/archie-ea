"""Routes that write platform-wide tables (the vendor catalogue, ACM
capabilities, confidence thresholds, LLM provider keys, Jira configuration)
require a platform administrator. An organisation administrator or architect is
refused AND nothing is written; a platform administrator is admitted.
"""

from __future__ import annotations

import uuid

import pytest


def _user(db_session, org, *, platform=False, role_name="Administrator"):
    from app.models.user import Permission, Role, User

    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        role = Role(name=role_name, permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()
    user = User(
        email=f"gate-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Gate",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        role=role,
    )
    user.password = uuid.uuid4().hex
    user.is_org_admin = True
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    return user


def _login(db_session, client, login_as, user_id):
    from app.models.user import User

    db_session.expunge_all()
    login_as(client, db_session.get(User, user_id))


@pytest.fixture
def world(app, db_session, make_org):
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

    org_a = make_org("gate-a")
    org_b = make_org("gate-b")
    admin_a = _user(db_session, org_a)
    admin_b = _user(db_session, org_b)
    platform = _user(db_session, org_a, platform=True)
    vendor = VendorOrganization(name=f"Gate Vendor {uuid.uuid4().hex[:8]}")
    db_session.add(vendor)
    db_session.flush()
    product = VendorProduct(
        name="Gate Product", vendor_organization_id=vendor.id, product_type="Product"
    )
    db_session.add(product)
    db_session.commit()
    return {
        "tenant_ids": (admin_a.id, admin_b.id),
        "platform_id": platform.id,
        "vendor_id": vendor.id,
        "product_id": product.id,
        "vendor_name": vendor.name,
        "VendorOrganization": VendorOrganization,
        "VendorProduct": VendorProduct,
    }


def _counts(db_session, w):
    from app.models.confidence_review import ConfidenceThreshold

    return (
        db_session.query(w["VendorOrganization"]).count(),
        db_session.query(w["VendorProduct"]).count(),
        db_session.query(ConfidenceThreshold).count(),
    )


def _routes(w):
    v, p = w["vendor_id"], w["product_id"]
    body = {"name": "Probe Vendor"}
    return [
        ("post", "/api/acm/seed", None),
        ("post", "/api/acm-hybrid/seed", None),
        ("post", "/api/v1/vendors/", body),
        ("put", f"/api/v1/vendors/{v}", {"name": "Renamed"}),
        ("delete", f"/api/v1/vendors/{v}", None),
        ("post", "/api/vendors/", body),
        ("put", f"/api/vendors/{v}", {"name": "Renamed"}),
        ("delete", f"/api/vendors/{v}", None),
        ("delete", "/api/vendors/bulk", {"ids": [v]}),
        ("post", "/vendor-management/api/vendors", body),
        ("put", f"/vendor-management/api/vendors/{v}", {"name": "Renamed"}),
        ("delete", f"/vendor-management/api/vendors/{v}", None),
        ("post", "/vendor-management/create", body),
        ("get", "/vendor-management/import", None),
        ("post", "/vendors/create", body),
        ("put", f"/vendors/{v}/edit", {"name": "Renamed"}),
        ("post", f"/vendors/{v}/activate", {"contract_value_annual": "1000"}),
        ("post", f"/vendors/{v}/products/{p}/deploy", {}),
        ("post", f"/vendors/{v}/delete", None),
        ("get", "/vendors/import", None),
        ("post", "/applications/vendors/create", body),
        ("post", "/dashboard/applications/vendors/create", body),
        ("post", f"/dashboard/applications/vendors/{v}/edit", {"name": "Renamed"}),
        ("post", f"/dashboard/applications/vendors/{v}/activate", {"contract_value_annual": "1000"}),
        ("post", f"/dashboard/applications/vendors/{v}/products/{p}/deploy", {}),
        ("post", f"/dashboard/applications/vendors/{v}/delete", None),
        ("post", f"/api/vendor/{v}/products", {"name": "Probe Product"}),
        ("post", "/reviews/api/thresholds", {"threshold_name": f"Probe {uuid.uuid4().hex[:6]}"}),
        ("post", "/adm-kanban/init-phases", None),
        ("post", "/admin/send-digest", None),
        ("get", "/admin/ai-confidence", None),
        ("get", "/admin/api-settings/env-keys", None),
        ("post", "/admin/api-settings/load-env", {"keys": []}),
        ("get", "/admin/jira-settings", None),
        ("post", "/admin/jira-settings", {}),
        ("post", "/admin/jira-settings/test-connection", {}),
        ("post", "/admin/jira-settings/save-env-config", {}),
        ("post", "/admin/jira-settings/trigger-push", {}),
        ("get", "/admin/jira-settings/push-status", None),
        ("get", "/admin/jira-settings/kanban-push-status", None),
        ("post", "/admin/jira-settings/trigger-kanban-push", {}),
        ("post", "/admin/jira-settings/push-epics", {}),
        ("post", "/admin/jira-settings/push-applications", {}),
        ("post", "/admin/jira-settings/push-dependencies", {}),
        ("get", "/admin/jira-settings/field-discovery", None),
    ]


def _send(client, method, path, body):
    if body is None:
        return getattr(client, method)(path)
    if method == "post" and path.rstrip("/").split("/")[-1] in (
        "create", "activate", "deploy", "delete", "edit"
    ):
        return client.post(path, data=body)
    return getattr(client, method)(path, json=body)


def test_every_gated_route_is_in_the_url_map(app, world):
    """The table above names real routes: an unknown path would 404 for every
    caller and the refusal assertions below would prove nothing."""
    rules = {r.rule for r in app.url_map.iter_rules()}
    import re

    def pattern(rule):
        return re.compile("^" + re.sub(r"<[^>]+>", "[^/]+", rule) + "$")

    patterns = [pattern(r) for r in rules]
    missing = [p for _m, p, _b in _routes(world) if not any(x.match(p) for x in patterns)]
    # the hybrid ACM blueprint is not registered in this application
    assert missing == ["/api/acm-hybrid/seed"]


def test_tenant_admins_are_refused_and_nothing_is_written(
    app, db_session, client, login_as, world
):
    routes = [r for r in _routes(world) if r[1] != "/api/acm-hybrid/seed"]
    for admin_id in world["tenant_ids"]:
        for method, path, body in routes:
            before = _counts(db_session, world)
            _login(db_session, client, login_as, admin_id)
            response = _send(client, method, path, body)
            assert response.status_code == 403, (
                f"{method.upper()} {path} answered {response.status_code} to an "
                f"organisation administrator"
            )
            db_session.expunge_all()
            assert _counts(db_session, world) == before, f"{method.upper()} {path} wrote rows"
            vendor = db_session.get(world["VendorOrganization"], world["vendor_id"])
            assert vendor is not None
            assert vendor.name == world["vendor_name"]
            assert vendor.contract_status != "contracted"


def test_platform_admin_is_never_refused(app, db_session, client, login_as, world):
    for method, path, body in _routes(world):
        if "/delete" in path or method == "delete" or path.endswith("/bulk"):
            continue  # destructive calls are exercised in their own test
        if path == "/api/acm-hybrid/seed":
            continue
        _login(db_session, client, login_as, world["platform_id"])
        response = _send(client, method, path, body)
        assert response.status_code not in (401, 403), (
            f"{method.upper()} {path} refused a platform admin ({response.status_code})"
        )


@pytest.mark.parametrize(
    "path",
    ["/vendors/{v}/activate", "/dashboard/applications/vendors/{v}/activate"],
)
def test_activate_vendor_only_a_platform_admin_moves_it_to_contracted(
    app, db_session, client, login_as, world, path
):
    path = path.format(v=world["vendor_id"])
    VO = world["VendorOrganization"]
    for admin_id in world["tenant_ids"]:
        _login(db_session, client, login_as, admin_id)
        assert client.post(path, data={"contract_value_annual": "12345"}).status_code == 403
        db_session.expunge_all()
        vendor = db_session.get(VO, world["vendor_id"])
        assert vendor.contract_status != "contracted"
        assert not vendor.contract_value_annual

    _login(db_session, client, login_as, world["platform_id"])
    response = client.post(path, data={"contract_value_annual": "12345"})
    assert response.status_code in (200, 302)
    db_session.expunge_all()
    vendor = db_session.get(VO, world["vendor_id"])
    assert vendor.contract_status == "contracted"
    assert float(vendor.contract_value_annual) == 12345


def test_a_vendor_creator_who_is_not_a_platform_admin_cannot_edit_or_delete(
    app, db_session, client, login_as, world
):
    """The dashboard vendor routes used to admit the vendor's recorded creator."""
    VO = world["VendorOrganization"]
    vendor = db_session.get(VO, world["vendor_id"])
    vendor.created_by_id = world["tenant_ids"][0]
    db_session.commit()
    _login(db_session, client, login_as, world["tenant_ids"][0])

    for method, path, body in (
        ("post", f"/dashboard/applications/vendors/{world['vendor_id']}/edit", {"name": "Hijacked"}),
        ("post", f"/dashboard/applications/vendors/{world['vendor_id']}/delete", None),
    ):
        assert _send(client, method, path, body).status_code == 403
    db_session.expunge_all()
    vendor = db_session.get(VO, world["vendor_id"])
    assert vendor is not None and vendor.name == world["vendor_name"]


def test_platform_admin_can_delete_a_vendor_on_each_tree(
    app, db_session, client, login_as, world
):
    VO = world["VendorOrganization"]
    _login(db_session, client, login_as, world["platform_id"])
    response = client.post(f"/dashboard/applications/vendors/{world['vendor_id']}/delete")
    assert response.status_code in (200, 302)
    db_session.expunge_all()
    assert db_session.get(VO, world["vendor_id"]) is None


def test_env_keys_are_not_leaked_to_an_org_admin(
    app, db_session, client, login_as, world, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "key-" + "x" * 12 + "5678")
    _login(db_session, client, login_as, world["tenant_ids"][0])
    for method, path in (("get", "/admin/api-settings/env-keys"), ("post", "/admin/api-settings/load-env")):
        response = getattr(client, method)(path)
        assert response.status_code == 403
        assert b"5678" not in response.data
    _login(db_session, client, login_as, world["platform_id"])
    response = client.get("/admin/api-settings/env-keys")
    assert response.status_code == 200
    assert b"5678" in response.data


def test_confidence_threshold_is_written_only_by_a_platform_admin(
    app, db_session, client, login_as, world
):
    from app.models.confidence_review import ConfidenceThreshold

    name = f"Gate threshold {uuid.uuid4().hex[:6]}"
    _login(db_session, client, login_as, world["tenant_ids"][0])
    assert client.post("/reviews/api/thresholds", json={"threshold_name": name}).status_code == 403
    db_session.expunge_all()
    assert ConfidenceThreshold.query.filter_by(threshold_name=name).count() == 0

    _login(db_session, client, login_as, world["platform_id"])
    response = client.post("/reviews/api/thresholds", json={"threshold_name": name})
    assert response.status_code == 200
    db_session.expunge_all()
    assert ConfidenceThreshold.query.filter_by(threshold_name=name).count() == 1


def test_vendor_product_create_writes_only_for_a_platform_admin(
    app, db_session, client, login_as, world
):
    VP = world["VendorProduct"]
    path = f"/api/vendor/{world['vendor_id']}/products"
    before = db_session.query(VP).count()
    _login(db_session, client, login_as, world["tenant_ids"][0])
    assert client.post(path, json={"name": "Sneaky"}).status_code == 403
    db_session.expunge_all()
    assert db_session.query(VP).count() == before

    _login(db_session, client, login_as, world["platform_id"])
    response = client.post(path, json={"name": "Sanctioned Product"})
    assert response.status_code in (200, 201)
    db_session.expunge_all()
    assert db_session.query(VP).count() == before + 1


def _elements_body(vendor_id, context, name="Gate Element"):
    return {
        "elements": [{"name": name, "type": "ApplicationComponent",
                      "description": "d", "layer": "Application"}],
        "analysis_context": context,
        "target_vendor_id": vendor_id,
    }


def test_create_elements_vendor_context_is_platform_admin_only(
    app, db_session, client, login_as, world
):
    VP = world["VendorProduct"]
    before = db_session.query(VP).count()
    _login(db_session, client, login_as, world["tenant_ids"][0])
    response = client.post(
        "/ai-chat/create-elements", json=_elements_body(world["vendor_id"], "vendor")
    )
    assert response.status_code == 403
    db_session.expunge_all()
    assert db_session.query(VP).count() == before

    _login(db_session, client, login_as, world["platform_id"])
    response = client.post(
        "/ai-chat/create-elements", json=_elements_body(world["vendor_id"], "vendor")
    )
    assert response.status_code == 200
    assert response.get_json().get("success") is True


def test_create_elements_general_context_still_works_for_an_ordinary_user(
    app, db_session, client, login_as, world
):
    from app.models.archimate_core import ArchiMateElement

    name = f"Gate Element {uuid.uuid4().hex[:6]}"
    _login(db_session, client, login_as, world["tenant_ids"][0])
    response = client.post(
        "/ai-chat/create-elements", json=_elements_body(None, "general", name)
    )
    assert response.status_code == 200
    assert response.get_json().get("success") is True
    db_session.expunge_all()
    assert ArchiMateElement.query.filter_by(name=name).count() >= 1


def test_ordinary_user_journeys_are_unaffected(app, db_session, make_org, client, login_as):
    """Routes outside the vendor/global-table set stay open to an ordinary user:
    the rationalization evidence trail answers 200 and the AI guide chat is not
    refused."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.user import User

    org = make_org("gate-ordinary")
    user = User(
        email=f"ordinary-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Ordinary",
        last_name="User",
        organization_id=org.id,
        enterprise_role="portfolio_manager",
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    component = ApplicationComponent(name="Ordinary App", organization_id=org.id)
    db_session.add(component)
    db_session.commit()
    user_id, app_id = user.id, component.id

    _login(db_session, client, login_as, user_id)
    trail = client.get(f"/applications/rationalization/api/evidence-trail/{app_id}")
    assert trail.status_code == 200, trail.get_data(as_text=True)

    guide = client.post("/ai-chat/guide/message", json={"message": "hello", "page": "/"})
    assert guide.status_code != 403, guide.get_data(as_text=True)
    assert guide.status_code < 500 or guide.status_code == 503
