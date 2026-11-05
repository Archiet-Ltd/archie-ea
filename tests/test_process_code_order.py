"""pcord01: process codes read in natural order wherever a list of them is returned.

The database collation (en_US.utf8 everywhere Entelim runs) ignores the dots, so
``ORDER BY process_code`` puts 10.0 between 1.1 and 1.2. Every list below must read
1.1, 1.2, 2.1, 10.0, 10.2a. The tests need a database created with that collation.
"""
from __future__ import annotations

import uuid

import pytest

NATURAL = ["1.1", "1.2", "2.1", "10.0", "10.2a"]
SCRAMBLED = ["10.2a", "2.1", "1.2", "10.0", "1.1"]


@pytest.fixture(autouse=True)
def _en_us_database(app, _schema):
    from app import db

    with app.app_context():
        collate = db.session.execute(
            db.text("select datcollate from pg_database where datname = current_database()")
        ).scalar()
    if "en_US" not in (collate or ""):
        pytest.fail(f"these tests need an en_US database; this one is {collate!r}")


def _org_and_user(db_session, make_org, label):
    from app.models.user import User

    org = make_org(label)
    user = User(email=f"{label}-{uuid.uuid4().hex[:6]}@example.test", first_name="O", last_name="R",
                organization_id=org.id, confirmed=True)
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.commit()
    return org, user


def _processes(db_session, codes=SCRAMBLED, **extra):
    from app.models.apqc_process import APQCProcess

    rows = [APQCProcess(process_code=c, process_name=f"Order {c}", **extra) for c in codes]
    db_session.add_all(rows)
    db_session.flush()
    return rows


def _ours(items, rows, code_key="process_code", id_key="id"):
    mine = {r.id for r in rows}
    return [i[code_key] for i in items if i.get(id_key) in mine]


def _get(client, login_as, user, url):
    login_as(client, user)
    resp = client.get(url)
    assert resp.status_code == 200, (url, resp.status_code, resp.data[:300])
    return resp.get_json()


# --- the key and the helper ------------------------------------------------

def test_the_one_key_orders_the_acceptance_codes():
    from app.models.apqc_process import process_code_sort_key

    assert sorted(SCRAMBLED, key=process_code_sort_key) == NATURAL


def test_tie_break_is_name_then_id():
    from app.models.apqc_process import sort_processes_naturally

    class P:
        def __init__(self, id, code, name):
            self.id, self.process_code, self.process_name = id, code, name

    rows = [P(3, "1.1", "b"), P(2, "1.1", "b"), P(1, "1.1", "a"), P(4, "1.0", "z")]
    assert [p.id for p in sort_processes_naturally(rows)] == [4, 1, 2, 3]


# --- vendor REST api (/api/vendors/...) -------------------------------------

def test_vendor_apqc_process_list(db_session, make_org, client, login_as):
    _, user = _org_and_user(db_session, make_org, "vlist")
    rows = _processes(db_session)
    db_session.commit()
    body = _get(client, login_as, user, "/api/vendors/apqc/processes")
    assert _ours(body, rows) == NATURAL


def test_vendor_portfolio_overview_lists_processes_naturally(db_session, make_org, client, login_as):
    _, user = _org_and_user(db_session, make_org, "vover")
    rows = _processes(db_session)
    db_session.commit()
    body = _get(client, login_as, user, "/api/vendors/analytics/overview")
    assert _ours(body["apqc_processes"], rows) == NATURAL


def test_vendor_comparison_matrix_rows_in_natural_order(db_session, make_org, client, login_as):
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

    _, user = _org_and_user(db_session, make_org, "vcomp")
    rows = _processes(db_session)
    vendor = VendorOrganization(name=f"Vendor {uuid.uuid4().hex[:8]}")
    db_session.add(vendor)
    db_session.flush()
    product = VendorProduct(vendor_organization_id=vendor.id, name="Prod")
    db_session.add(product)
    db_session.commit()
    body = _get(client, login_as, user, f"/api/vendors/comparison/apqc?product_ids={product.id}")
    matrix = body["comparison_matrix"] if "comparison_matrix" in body else body["matrix"]
    assert _ours(matrix, rows, id_key="process_id") == NATURAL


# --- solution picker (limit 500) -------------------------------------------

def _solution(db_session, org, user):
    from app.models.solution_models import Solution

    sol = Solution(name=f"Sol {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(sol)
    db_session.commit()
    return sol


def test_solution_picker_lists_processes_naturally(db_session, make_org, client, login_as):
    org, user = _org_and_user(db_session, make_org, "spick")
    sol = _solution(db_session, org, user)
    rows = _processes(db_session)
    db_session.commit()
    body = _get(client, login_as, user, f"/solutions/{sol.id}/all-apqc-processes")
    assert [i["sub"] for i in body["items"] if i["id"] in {r.id for r in rows}] == NATURAL


def test_solution_picker_first_500_are_the_natural_first_500(db_session, make_org, client, login_as):
    from app.models.apqc_process import APQCProcess, process_code_sort_key

    org, user = _org_and_user(db_session, make_org, "s600")
    sol = _solution(db_session, org, user)
    # 600 codes inserted highest-first, so neither id order nor collation order is natural order
    codes = [f"{g}.{h}" for g in range(1, 13) for h in range(0, 50)]
    rows = _processes(db_session, codes=list(reversed(codes)))
    db_session.commit()
    body = _get(client, login_as, user, f"/solutions/{sol.id}/all-apqc-processes")
    mine = {r.id for r in rows}
    got = [i["sub"] for i in body["items"] if i["id"] in mine]
    total_catalogue = APQCProcess.query.count()
    expected = sorted(
        [c for (c,) in APQCProcess.query.with_entities(APQCProcess.process_code).all()],
        key=process_code_sort_key,
    )[:500]
    assert len(body["items"]) == min(500, total_catalogue)
    assert [i["sub"] for i in body["items"]] == expected
    assert got == [c for c in expected if c in set(codes)]


# --- dashboard apqc pickers ------------------------------------------------

def test_dashboard_process_list_is_natural(db_session, make_org, client, login_as):
    _, user = _org_and_user(db_session, make_org, "dlist")
    rows = _processes(db_session)
    db_session.commit()
    body = _get(client, login_as, user, "/dashboard/api/apqc-processes")
    assert _ours(body, rows) == NATURAL


def test_dashboard_level_one_options_are_natural(db_session, make_org, client, login_as):
    _, user = _org_and_user(db_session, make_org, "dl1")
    codes = ["10.0", "2.0", "1.0", "13.0", "9.0"]
    rows = _processes(db_session, codes=codes, category_level_1="Level one")
    db_session.commit()
    body = _get(client, login_as, user, "/dashboard/api/apqc-processes?level=1")
    assert _ours(body, rows) == ["1.0", "2.0", "9.0", "10.0", "13.0"]


def test_dashboard_children_are_natural(db_session, make_org, client, login_as):
    _, user = _org_and_user(db_session, make_org, "dkids")
    parent = _processes(db_session, codes=["7.0"])[0]
    kids = _processes(db_session, codes=["7.10", "7.2", "7.1", "7.9"])
    db_session.commit()
    login_as(client, user)
    resp = client.post("/dashboard/api/apqc-processes/children",
                       json={"parent_ids": [parent.id], "target_level": 2})
    assert resp.status_code == 200, resp.data[:300]
    assert _ours(resp.get_json(), kids) == ["7.1", "7.2", "7.9", "7.10"]


# --- tree screen api -------------------------------------------------------

def test_apqc_tree_siblings_are_natural(db_session, make_org, client, login_as):
    _, user = _org_and_user(db_session, make_org, "tree")
    rows = _processes(db_session)
    db_session.commit()
    body = _get(client, login_as, user, "/capability-map/api/trees/apqc")
    mine = {r.id for r in rows}
    assert [n["process_code"] for n in body["children"] if n["id"] in mine] == NATURAL


# --- cache, semantic index, industry framework -----------------------------

def test_cached_catalogue_list_is_natural(app, db_session, make_org):
    from app.services.core import data_cache

    _processes(db_session)
    db_session.commit()
    data_cache.invalidate_apqc_cache()
    try:
        got = [p["process_code"] for p in data_cache.get_all_apqc_processes()]
    finally:
        data_cache.invalidate_apqc_cache()
    assert [c for c in got if c in set(NATURAL)] == NATURAL


def test_semantic_index_reads_processes_naturally(app, db_session):
    from app.services.semantic_apqc_service import SemanticAPQCService

    rows = _processes(db_session)
    db_session.commit()
    svc = SemanticAPQCService.__new__(SemanticAPQCService)
    svc._process_cache = None
    svc._cache_timestamp = None
    svc._cache_ttl = 3600
    got = [p.process_code for p in svc._get_apqc_processes() if p.id in {r.id for r in rows}]
    assert got == NATURAL


def _framework(db_session, code):
    from app.models.industry_apqc import IndustryAPQCFramework

    fw = IndustryAPQCFramework(industry_code=code, industry_name=f"Industry {code}", is_active=True)
    db_session.add(fw)
    db_session.flush()
    return fw


def _industry_rows(db_session, fw, codes, label, org_id):
    from app.models.industry_apqc import IndustryAPQCProcess

    rows = []
    for c in codes:
        row = IndustryAPQCProcess(industry_framework_id=fw.id, industry_process_code=c,
                                  industry_process_name=f"{label} {c}", is_active=True,
                                  organization_id=org_id)
        db_session.add(row)
        rows.append(row)
    db_session.flush()
    return rows


def test_industry_framework_processes_are_natural(db_session, make_org, client, login_as):
    org, user = _org_and_user(db_session, make_org, "ind")
    fw = _framework(db_session, "ZZT")
    _industry_rows(db_session, fw, SCRAMBLED, "Ind", org.id)
    db_session.commit()
    body = _get(client, login_as, user, "/industry-apqc/api/frameworks/ZZT/processes")
    assert [p["industry_process_code"] for p in body["processes"]] == NATURAL


# --- isolation -------------------------------------------------------------

def test_a_second_organisations_industry_processes_never_change_the_order_of_the_first_ones(
    db_session, make_org, client, login_as
):
    org_a, user_a = _org_and_user(db_session, make_org, "isoa")
    org_b, _ = _org_and_user(db_session, make_org, "isob")
    fw = _framework(db_session, "ZZA")
    _industry_rows(db_session, fw, SCRAMBLED, "A", org_a.id)
    _industry_rows(db_session, fw, ["1.15", "3.0", "10.1", "1.1"], "B", org_b.id)
    db_session.commit()
    body = _get(client, login_as, user_a, "/industry-apqc/api/frameworks/ZZA/processes")
    # A second organisation's rows (whatever the read fence does with them) never
    # change where the first organisation's own rows fall.
    mine = [p["industry_process_code"] for p in body["processes"] if p["industry_process_name"].startswith("A ")]
    assert mine == NATURAL
