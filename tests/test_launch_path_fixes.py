"""Launch-path fixes: .xlsx import accepted by structure, work package create
returns a plain result or error, and knowledge-graph search works per organisation."""

from __future__ import annotations

import io
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label):
    from app.models.user import Role, User

    Role.insert_roles()
    role_obj = Role.query.filter_by(name="User").first()
    user = User(
        email=f"lp-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Test",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="portfolio_manager",
        role_id=role_obj.id if role_obj else None,
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def two_orgs(db_session, make_org):
    a, b = make_org("lp-a"), make_org("lp-b")
    return {"a": a, "b": b, "ua": _user(db_session, a, "a"), "ub": _user(db_session, b, "b")}


# ---------------------------------------------------------------- xlsx import

def _openpyxl_bytes():
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(["Name", "Vendor"])
    wb.active.append(["App One", "Acme"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _xlsxwriter_bytes():
    xlsxwriter = pytest.importorskip("xlsxwriter")
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    ws = wb.add_worksheet()
    ws.write_row(0, 0, ["Name", "Vendor"])
    ws.write_row(1, 0, ["App Two", "Acme"])
    wb.close()
    return buf.getvalue()


@pytest.mark.parametrize("maker", [_openpyxl_bytes, _xlsxwriter_bytes])
def test_real_xlsx_files_pass_the_type_check(maker):
    from werkzeug.datastructures import FileStorage

    from app.utils.file_validation import XLSX_MIME, validate_mime_type

    f = FileStorage(stream=io.BytesIO(maker()), filename="apps.xlsx")
    assert validate_mime_type(f, "apps.xlsx") == XLSX_MIME
    assert f.read(2) == b"PK"  # stream left at the start


@pytest.mark.parametrize(
    "payload",
    [b"this is not a workbook, just text", b"PK\x03\x04 broken zip", b""],
)
def test_invalid_file_named_xlsx_is_rejected(payload):
    from werkzeug.datastructures import FileStorage

    from app.utils.file_validation import InvalidFileTypeError, validate_mime_type

    f = FileStorage(stream=io.BytesIO(payload), filename="apps.xlsx")
    with pytest.raises(InvalidFileTypeError):
        validate_mime_type(f, "apps.xlsx")


def test_plain_zip_named_xlsx_is_rejected():
    import zipfile

    from werkzeug.datastructures import FileStorage

    from app.utils.file_validation import InvalidFileTypeError, validate_mime_type

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "hello")
    f = FileStorage(stream=io.BytesIO(buf.getvalue()), filename="apps.xlsx")
    with pytest.raises(InvalidFileTypeError):
        validate_mime_type(f, "apps.xlsx")


def test_import_route_shows_plain_error_for_invalid_xlsx(two_orgs, client, login_as):
    login_as(client, two_orgs["ua"])
    resp = client.post(
        "/applications/import",
        data={"file": (io.BytesIO(b"not a workbook"), "apps.xlsx")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        flashes = sess.get("_flashes", [])
    assert any(cat == "error" and "Invalid file type" in msg for cat, msg in flashes), flashes


def test_import_route_does_not_reject_real_xlsx_as_a_type_error(two_orgs, client, login_as):
    login_as(client, two_orgs["ua"])
    client.post(
        "/applications/import",
        data={"file": (io.BytesIO(_openpyxl_bytes()), "apps.xlsx")},
        content_type="multipart/form-data",
    )
    with client.session_transaction() as sess:
        flashes = sess.get("_flashes", [])
    assert not any("Invalid file type" in msg for _cat, msg in flashes), flashes


# ------------------------------------------------------- work package create

_WP = "/api/roadmap/work-packages"


def test_work_package_create_valid_and_isolated(two_orgs, db_session, client, login_as):
    from app.models.unified_capability import UnifiedCapability

    db_session.add(UnifiedCapability(
        name="Billing", organization_id=two_orgs["a"].id,
        source_table="business_capability", source_id="9001",
        source_org_id=two_orgs["a"].id,
    ))
    db_session.flush()
    login_as(client, two_orgs["ua"])
    resp = client.post(_WP, json={"name": "Alpha Package", "business_capability": "Billing"})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    wp_id = resp.get_json()["work_package"]["id"]

    login_as(client, two_orgs["ub"])
    resp_b = client.get(f"{_WP}/{wp_id}")
    assert resp_b.status_code == 404
    assert "Alpha Package" not in client.get(_WP).get_data(as_text=True)


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "No capability"},
        {"name": "", "business_capability": "Billing"},
        {"name": "Bad dates", "business_capability": "Billing",
         "start_date": "2026-12-01", "end_date": "2026-01-01"},
    ],
)
def test_work_package_create_invalid_is_clean_4xx(two_orgs, client, login_as, payload):
    login_as(client, two_orgs["ua"])
    resp = client.post(_WP, json=payload)
    assert 400 <= resp.status_code < 500, resp.get_data(as_text=True)
    body = resp.get_data(as_text=True)
    for leak in ("Traceback", "subscriptable", "ValidationResult", "object has no"):
        assert leak not in body


# --------------------------------------------------------------- KG search

_KG = "/api/v2/enterprise/kg/search"


def test_kg_search_scoped_to_caller_organisation(two_orgs, db_session, client, login_as):
    from app.models.archimate_core import ArchiMateElement

    for org, name in ((two_orgs["a"], "Zeta Ledger A"), (two_orgs["b"], "Zeta Ledger B")):
        db_session.add(ArchiMateElement(name=name, type="ApplicationComponent",
                                        layer="application", organization_id=org.id))
    db_session.flush()

    login_as(client, two_orgs["ua"])
    resp = client.get(_KG, query_string={"q": "zeta"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert [e["name"] for e in resp.get_json()["data"]] == ["Zeta Ledger A"]

    login_as(client, two_orgs["ub"])
    names = [e["name"] for e in client.get(_KG, query_string={"q": "zeta"}).get_json()["data"]]
    assert names == ["Zeta Ledger B"]


def test_kg_search_empty_query_is_a_plain_400(two_orgs, client, login_as):
    login_as(client, two_orgs["ua"])
    resp = client.get(_KG, query_string={"q": "  "})
    assert resp.status_code == 400
    assert "required" in resp.get_json()["error"].lower()


def test_kg_search_wildcard_characters_are_literal(two_orgs, db_session, client, login_as):
    from app.models.archimate_core import ArchiMateElement

    db_session.add(ArchiMateElement(name="Plain Name", type="Node", layer="technology",
                                    organization_id=two_orgs["a"].id))
    db_session.flush()
    login_as(client, two_orgs["ua"])
    resp = client.get(_KG, query_string={"q": "%"})
    assert resp.status_code == 200
    assert resp.get_json()["data"] == []
