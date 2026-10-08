"""GET /business-model/api/list, /business-model/api/<id>,
/business-case/api/list, /business-case/api/<id>.

Covers:
  - list and detail return the caller's own-organisation rows
  - a foreign-organisation id and an absent id give identical 404 status/body
  - a missing name/title/description is JSON null, never "" or 0
  - every field the detail page renders is present in the detail JSON
"""
import uuid

import pytest


def _org_and_user(db_session, make_org, label):
    from app.models.user import User

    org = make_org(label)
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name=label,
        organization_id=org.id,
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return org, user


def _canvas(db_session, org, name="Canvas", description="A canvas"):
    from app.models.business_model import BusinessModelCanvas

    canvas = BusinessModelCanvas(
        name=name, description=description, organization_id=org.id,
    )
    db_session.add(canvas)
    db_session.flush()
    return canvas


def _business_case(db_session, org, title="Case", description="A case"):
    from app.models.business_case import BusinessCase

    case = BusinessCase(
        title=title, description=description, status="draft", organization_id=org.id,
    )
    db_session.add(case)
    db_session.flush()
    return case


def _unwrap(payload):
    """Both routes wrap payloads via success_response(); unwrap data ?? whole body."""
    return payload.get("data", payload)


@pytest.fixture
def two_orgs_users(db_session, make_org):
    org_a, user_a = _org_and_user(db_session, make_org, "canvas-a")
    org_b, user_b = _org_and_user(db_session, make_org, "canvas-b")
    return org_a, user_a, org_b, user_b


# --------------------------------------------------------------------------
# Business Model Canvas
# --------------------------------------------------------------------------


def test_canvas_api_list_returns_own_org_rows_only(db_session, two_orgs_users, client, login_as):
    org_a, user_a, org_b, _user_b = two_orgs_users
    _canvas(db_session, org_a, name="A's canvas")
    _canvas(db_session, org_b, name="B's canvas")
    db_session.commit()

    login_as(client, user_a)
    resp = client.get("/business-model/api/list")
    assert resp.status_code == 200
    names = [row["name"] for row in _unwrap(resp.get_json())]
    assert "A's canvas" in names
    assert "B's canvas" not in names


def test_canvas_api_list_missing_name_and_description_are_null(db_session, two_orgs_users, client, login_as):
    org_a, user_a, _org_b, _user_b = two_orgs_users
    _canvas(db_session, org_a, name=None, description=None)
    db_session.commit()

    login_as(client, user_a)
    resp = client.get("/business-model/api/list")
    assert resp.status_code == 200
    rows = _unwrap(resp.get_json())
    assert len(rows) == 1
    assert rows[0]["name"] is None, f"missing name must be JSON null, got {rows[0]['name']!r}"
    assert rows[0]["description"] is None, (
        f"missing description must be JSON null, got {rows[0]['description']!r}"
    )


def test_canvas_api_detail_returns_all_page_rendered_fields(db_session, two_orgs_users, client, login_as):
    """Every field business_model/detail.html renders must be in the detail JSON:
    name, description, operating_model_type, the 9 canvas blocks, timestamps."""
    from app.models.business_model import CANVAS_BLOCKS

    org_a, user_a, _org_b, _user_b = two_orgs_users
    canvas = _canvas(db_session, org_a, name="Full canvas")
    canvas.operating_model_type = "unification"
    canvas.key_partners = "Partner X"
    db_session.commit()

    login_as(client, user_a)
    resp = client.get(f"/business-model/api/{canvas.id}")
    assert resp.status_code == 200
    data = _unwrap(resp.get_json())

    for field in ("id", "name", "description", "operating_model_type", "created_at", "updated_at"):
        assert field in data, f"detail JSON missing rendered field {field!r}"
    for block_key in CANVAS_BLOCKS:
        assert block_key in data, f"detail JSON missing rendered canvas block {block_key!r}"
    assert data["operating_model_type"] == "unification"
    assert data["key_partners"] == "Partner X"


def test_canvas_api_detail_missing_value_is_null(db_session, two_orgs_users, client, login_as):
    org_a, user_a, _org_b, _user_b = two_orgs_users
    canvas = _canvas(db_session, org_a, name="Canvas", description=None)
    db_session.commit()

    login_as(client, user_a)
    resp = client.get(f"/business-model/api/{canvas.id}")
    assert resp.status_code == 200
    data = _unwrap(resp.get_json())
    assert data["description"] is None
    assert data["key_partners"] is None


def test_canvas_api_detail_foreign_org_and_absent_id_identical_404(
    db_session, two_orgs_users, client, login_as
):
    org_a, user_a, org_b, _user_b = two_orgs_users
    foreign_canvas = _canvas(db_session, org_b, name="Not yours")
    absent_id = foreign_canvas.id + 1_000_000
    db_session.commit()

    login_as(client, user_a)
    resp_foreign = client.get(f"/business-model/api/{foreign_canvas.id}")
    login_as(client, user_a)
    resp_absent = client.get(f"/business-model/api/{absent_id}")

    assert resp_foreign.status_code == 404
    assert resp_absent.status_code == 404
    assert resp_foreign.status_code == resp_absent.status_code

    body_foreign = resp_foreign.get_json()
    body_absent = resp_absent.get_json()
    assert body_foreign["success"] == body_absent["success"] is False
    assert body_foreign["error"]["code"] == body_absent["error"]["code"]
    assert body_foreign["error"]["message"] == body_absent["error"]["message"]


# --------------------------------------------------------------------------
# Business Case
# --------------------------------------------------------------------------


def test_business_case_api_list_returns_own_org_rows_only(db_session, two_orgs_users, client, login_as):
    org_a, user_a, org_b, _user_b = two_orgs_users
    _business_case(db_session, org_a, title="A's case")
    _business_case(db_session, org_b, title="B's case")
    db_session.commit()

    login_as(client, user_a)
    resp = client.get("/business-case/api/list")
    assert resp.status_code == 200
    titles = [row["title"] for row in _unwrap(resp.get_json())]
    assert "A's case" in titles
    assert "B's case" not in titles


def test_business_case_api_list_missing_title_is_null(db_session, two_orgs_users, client, login_as):
    org_a, user_a, _org_b, _user_b = two_orgs_users
    _business_case(db_session, org_a, title=None, description=None)
    db_session.commit()

    login_as(client, user_a)
    resp = client.get("/business-case/api/list")
    assert resp.status_code == 200
    rows = _unwrap(resp.get_json())
    assert len(rows) == 1
    assert rows[0]["title"] is None, f"missing title must be JSON null, got {rows[0]['title']!r}"


def test_business_case_api_detail_returns_all_page_rendered_fields(
    db_session, two_orgs_users, client, login_as
):
    """Every field business_case/detail.html renders must be in the detail
    JSON: title, description, status, links, the document sections, the
    financial summary, and the computed npv."""
    org_a, user_a, _org_b, _user_b = two_orgs_users
    case = _business_case(db_session, org_a, title="Full case")
    case.problem_statement = "The problem"
    case.options_considered = "Option A vs B"
    case.recommended_option = "Option A"
    case.expected_benefits = "Faster onboarding"
    case.key_risks = "Vendor lock-in"
    db_session.commit()

    login_as(client, user_a)
    resp = client.get(f"/business-case/api/{case.id}")
    assert resp.status_code == 200
    data = _unwrap(resp.get_json())

    for field in (
        "id", "title", "description", "status",
        "capability_id", "capability_name",
        "strategic_initiative_id", "strategic_initiative_name",
        "solution_id", "solution_name",
        "problem_statement", "options_considered", "recommended_option",
        "expected_benefits", "key_risks",
        "financial_benefit_annual", "capex", "opex_annual", "tco_3yr",
        "roi_percentage", "payback_months", "npv",
        "created_at", "updated_at",
    ):
        assert field in data, f"detail JSON missing rendered field {field!r}"
    assert data["recommended_option"] == "Option A"


def test_business_case_api_detail_missing_financials_are_null_not_zero(
    db_session, two_orgs_users, client, login_as
):
    """A business case with no financial data entered must report null, not
    a fabricated 0 -- CLAUDE.md's fabricated-data rule (0 means "not
    computed" is indistinguishable from a measured zero)."""
    org_a, user_a, _org_b, _user_b = two_orgs_users
    case = _business_case(db_session, org_a, title="No financials yet")
    db_session.commit()

    login_as(client, user_a)
    resp = client.get(f"/business-case/api/{case.id}")
    assert resp.status_code == 200
    data = _unwrap(resp.get_json())

    for field in ("financial_benefit_annual", "capex", "opex_annual", "tco_3yr",
                  "roi_percentage", "payback_months", "npv"):
        assert data[field] is None, f"{field} must be null when not entered, got {data[field]!r}"


def test_business_case_api_detail_foreign_org_and_absent_id_identical_404(
    db_session, two_orgs_users, client, login_as
):
    org_a, user_a, org_b, _user_b = two_orgs_users
    foreign_case = _business_case(db_session, org_b, title="Not yours")
    absent_id = foreign_case.id + 1_000_000
    db_session.commit()

    login_as(client, user_a)
    resp_foreign = client.get(f"/business-case/api/{foreign_case.id}")
    login_as(client, user_a)
    resp_absent = client.get(f"/business-case/api/{absent_id}")

    assert resp_foreign.status_code == 404
    assert resp_absent.status_code == 404
    assert resp_foreign.status_code == resp_absent.status_code

    body_foreign = resp_foreign.get_json()
    body_absent = resp_absent.get_json()
    assert body_foreign["success"] == body_absent["success"] is False
    assert body_foreign["error"]["code"] == body_absent["error"]["code"]
    assert body_foreign["error"]["message"] == body_absent["error"]["message"]
