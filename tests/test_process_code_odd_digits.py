"""pcord01b: one odd process code can never break the catalogue, and the tree editor refuses it.

The natural-order key is now behind nine catalogue lists. A code carrying a digit that
``str.isdigit`` accepts but ``int`` rejects (a superscript two, an Arabic-Indic three)
used to raise ValueError and answer 500 on every one of them for every organisation.
"""
from __future__ import annotations

import uuid

import pytest

ODD = ["1.²", "1.٣", "1.1a"]


def test_key_accepts_non_decimal_digits_without_raising():
    from app.models.apqc_process import process_code_sort_key as key

    for code in ODD:
        assert key(code) is not None
    assert sorted(["10.0", "1.²", "2.1", "1.1", "1.٣", "1.1a"], key=key)  # no raise


def test_real_decimal_codes_keep_natural_order():
    from app.models.apqc_process import process_code_sort_key as key

    codes = ["10.2a", "2.1", "1.10", "1.2", "10.0", "1.1", "9.0", "13.0"]
    assert sorted(codes, key=key) == ["1.1", "1.2", "1.10", "2.1", "9.0", "10.0", "10.2a", "13.0"]


def test_non_ascii_digits_sort_as_text_after_numbers_in_the_same_position():
    from app.models.apqc_process import process_code_sort_key as key

    assert key("1.2") < key("1.²")
    assert key("1.٣") != key("1.3")


def _user(db_session, make_org, label, platform_admin=False):
    from app.models.user import Role, User

    org = make_org(label)
    user = User(email=f"{label}-{uuid.uuid4().hex[:6]}@example.test", first_name="O", last_name="D",
                organization_id=org.id, confirmed=True, enterprise_role="solution_architect")
    user.password = uuid.uuid4().hex
    if platform_admin:
        Role.insert_roles()
        user.role = Role.query.filter_by(name="Administrator").one()
        user.is_platform_admin = True
        user.is_org_admin = True
        user.enterprise_role = "platform_admin"
    db_session.add(user)
    db_session.commit()
    return org, user


def test_one_odd_row_leaves_every_catalogue_route_answering_200(db_session, make_org, client, login_as):
    from app.models.apqc_process import APQCProcess
    from app.models.solution_models import Solution
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct
    from app.services.core import data_cache
    from app.services.semantic_apqc_service import SemanticAPQCService

    org, user = _user(db_session, make_org, "odd")
    tag = uuid.uuid4().hex[:6]
    odd = APQCProcess(process_code="1.²", process_name=f"Odd {tag}", category_level_1="Odd L1")
    fine = APQCProcess(process_code="1.1", process_name=f"Fine {tag}", category_level_1="Odd L1")
    sol = Solution(name=f"Sol {tag}", organization_id=org.id)
    vendor = VendorOrganization(name=f"Vendor {tag}")
    db_session.add_all([odd, fine, sol, vendor])
    db_session.flush()
    product = VendorProduct(vendor_organization_id=vendor.id, name="Prod")
    db_session.add(product)
    db_session.commit()
    try:
        login_as(client, user)
        routes = [
            ("GET", "/api/vendors/apqc/processes", None),
            ("GET", "/api/vendors/analytics/overview", None),
            ("GET", f"/api/vendors/comparison/apqc?product_ids={product.id}", None),
            ("GET", f"/solutions/{sol.id}/all-apqc-processes", None),
            ("GET", "/dashboard/api/apqc-processes", None),
            ("GET", "/dashboard/api/apqc-processes?level=1", None),
            ("POST", "/dashboard/api/apqc-processes/children", {"parent_ids": [fine.id], "target_level": 2}),
            ("GET", "/capability-map/api/trees/apqc", None),
            ("GET", "/capability-map/tree/apqc", None),
        ]
        for method, url, body in routes:
            resp = client.post(url, json=body) if method == "POST" else client.get(url)
            assert resp.status_code == 200, (method, url, resp.status_code, resp.data[:200])
            if "analytics/overview" in url:
                assert "apqc_processes_error" not in (resp.get_json() or {}), url
        data_cache.invalidate_apqc_cache()
        try:
            assert data_cache.get_all_apqc_processes() is not None
        finally:
            data_cache.invalidate_apqc_cache()
        svc = SemanticAPQCService.__new__(SemanticAPQCService)
        svc._process_cache = None
        svc._cache_timestamp = None
        svc._cache_ttl = 3600
        assert svc._get_apqc_processes() is not None
    finally:
        db_session.rollback()
        for model, pk in ((VendorProduct, product.id), (VendorOrganization, vendor.id), (Solution, sol.id),
                          (APQCProcess, odd.id), (APQCProcess, fine.id)):
            model.query.filter_by(id=pk).delete()
        db_session.commit()
        data_cache.invalidate_apqc_cache()


@pytest.mark.parametrize("bad", ["1.²", "1.٣", "1.1-b", "1 2", "", "é1.1"])
def test_tree_editor_refuses_a_code_that_is_not_digits_dots_and_letters(db_session, make_org, client, login_as, bad):
    from app.models.apqc_process import APQCProcess

    _, admin = _user(db_session, make_org, "tadm", platform_admin=True)
    tag = uuid.uuid4().hex[:8]
    row = APQCProcess(process_code="5.5", process_name=f"Keep {tag}")
    db_session.add(row)
    db_session.commit()
    login_as(client, admin)
    created = client.post("/capability-map/api/trees/apqc", json={"name": f"New {tag}", "process_code": bad})
    assert created.status_code == 400, created.data[:200]
    assert "digits" in created.get_json()["error"]
    assert APQCProcess.query.filter_by(process_name=f"New {tag}").count() == 0
    patched = client.patch(f"/capability-map/api/trees/apqc/{row.id}", json={"process_code": bad})
    assert patched.status_code == 400, patched.data[:200]
    assert "digits" in patched.get_json()["error"]
    db_session.expire_all()
    try:
        assert APQCProcess.query.get(row.id).process_code == "5.5"
    finally:
        APQCProcess.query.filter_by(id=row.id).delete()
        db_session.commit()


def test_tree_editor_still_saves_a_plain_code(db_session, make_org, client, login_as):
    from app.models.apqc_process import APQCProcess

    _, admin = _user(db_session, make_org, "tok", platform_admin=True)
    tag = uuid.uuid4().hex[:8]
    login_as(client, admin)
    resp = client.post("/capability-map/api/trees/apqc", json={"name": f"Ok {tag}", "process_code": "10.2a"})
    assert resp.status_code == 201, resp.data[:200]
    try:
        assert APQCProcess.query.filter_by(process_name=f"Ok {tag}").one().process_code == "10.2a"
    finally:
        APQCProcess.query.filter_by(process_name=f"Ok {tag}").delete()
        db_session.commit()
