"""Every path that writes an application's status writes one from the one list.

The file import, the manual import, the create route, the chat's update tool and
the batch import commit store a value from `application_status.py`, matching any
case and spacing, and refuse anything else in words. Two organisations throughout:
nothing an import writes appears in the other organisation.
"""

from __future__ import annotations

import io
import uuid

import pytest

from tests.test_application_overview_save import _make_app, _make_user

pytestmark = pytest.mark.usefixtures("db_session")

ROWS = [("Live", "production"), ("planned", "design"), ("Retiring ", "retiring"),
        ("", None), ("Mystery", "REFUSED")]


@pytest.fixture
def orgs(db_session, make_org):
    org_a, org_b = make_org("stw-a"), make_org("stw-b")
    return {
        "org_a": org_a, "org_b": org_b,
        "user_a": _make_user(db_session, org_a, "usera"),
        "user_b": _make_user(db_session, org_b, "userb"),
    }


def _stored(db_session, org, names):
    from app.models.application_portfolio import ApplicationComponent

    db_session.expire_all()
    rows = ApplicationComponent.query.filter(
        ApplicationComponent.organization_id == org.id,
        ApplicationComponent.name.in_(names),
    ).all()
    return {row.name: row.deployment_status for row in rows}


def _names():
    tag = uuid.uuid4().hex[:6]
    return [f"Ledger {index} {tag}" for index in range(len(ROWS))]


def _assert_rows(db_session, orgs, names):
    stored = _stored(db_session, orgs["org_a"], names)
    for name, (_word, expected) in zip(names, ROWS):
        if expected == "REFUSED":
            assert name not in stored, "an unknown status must refuse its row"
        else:
            assert name in stored, f"{name} was not imported"
            assert stored[name] == expected
    assert _stored(db_session, orgs["org_b"], names) == {}


@pytest.mark.parametrize(
    "word, stored",
    [("Live", "production"), ("planned", "design"), ("Retiring ", "retiring"),
     ("  Production", "production"), ("Staging", "staging"), ("", None), (None, None)],
)
def test_a_known_word_is_stored_in_the_lists_form(word, stored):
    from app.services.application_status import status_for_write

    assert status_for_write(word) == (stored, None)


def test_an_unknown_word_is_refused_in_words():
    from app.services.application_status import status_for_write

    value, refusal = status_for_write("Mystery")
    assert value is None
    assert refusal.startswith("Status 'Mystery' is not one of: design, development")
    assert refusal.endswith("decommissioned")


def test_the_file_import_stores_the_lists_value_and_refuses_the_unknown(orgs, client, login_as,
                                                                         db_session):
    names = _names()
    csv = "Name,Status\n" + "".join(f"{n},{w}\n" for n, (w, _e) in zip(names, ROWS))
    login_as(client, orgs["user_a"])
    resp = client.post(
        "/applications/import",
        data={"file": (io.BytesIO(csv.encode()), "apps.csv"), "import_mode": "skip"},
        content_type="multipart/form-data", follow_redirects=True,
    )
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    # The page hands the message to its toast script, which escapes the quotes.
    assert "Status \\u0027Mystery\\u0027 is not one of: design, development" in page
    _assert_rows(db_session, orgs, names)


def test_the_file_import_keeps_a_stored_status_when_the_cell_is_blank(orgs, client, login_as,
                                                                      db_session):
    app_a = _make_app(db_session, orgs["org_a"], "Ledger")
    app_a.deployment_status = "staging"
    db_session.flush()
    csv = f"Name,Status\n{app_a.name},\n"
    login_as(client, orgs["user_a"])
    client.post(
        "/applications/import",
        data={"file": (io.BytesIO(csv.encode()), "apps.csv"), "import_mode": "update"},
        content_type="multipart/form-data", follow_redirects=True,
    )
    assert _stored(db_session, orgs["org_a"], [app_a.name]) == {app_a.name: "staging"}


def test_the_manual_import_stores_the_lists_value_and_refuses_the_unknown(orgs, client, login_as,
                                                                          db_session):
    login_as(client, orgs["user_a"])
    names = _names()
    for name, (word, expected) in zip(names, ROWS):
        resp = client.post(
            "/applications/import-manual",
            json={"applications": [{"name": name, "deployment_status": word}],
                  "duplicate_mode": "skip"},
        )
        if expected == "REFUSED":
            assert resp.status_code == 400
            assert "Status 'Mystery' is not one of: design" in resp.get_json()["error"]
        else:
            assert resp.status_code == 200, resp.get_json()
    _assert_rows(db_session, orgs, names)


def test_the_create_api_stores_the_lists_value_and_refuses_the_unknown(orgs, client, login_as,
                                                                       db_session):
    login_as(client, orgs["user_a"])
    names = _names()
    for name, (word, expected) in zip(names, ROWS):
        resp = client.post(
            "/applications/create",
            json={"name": name, "deployment_status": word},
        )
        if expected == "REFUSED":
            assert resp.status_code >= 400, resp.get_data(as_text=True)[:300]
        else:
            assert resp.status_code < 400, resp.get_data(as_text=True)[:300]
    _assert_rows(db_session, orgs, names)


@pytest.mark.parametrize("word, expected", [("Production", "production"),
                                            (" production ", "production"),
                                            ("production", "production")])
def test_the_chat_tool_matches_a_status_in_any_case_and_spacing(orgs, db_session, tenant_ctx,
                                                                word, expected):
    from app.modules.ai_chat.tools.executor import ToolExecutor

    app_a = _make_app(db_session, orgs["org_a"], "Ledger")
    with tenant_ctx(orgs["org_a"].id):
        executor = ToolExecutor.__new__(ToolExecutor)
        executor.user_id = orgs["user_a"].id
        executor._resolver = type("R", (), {"resolve_application": lambda self, name: {
            "resolved": True, "id": app_a.id, "name": app_a.name}})()
        result = executor._tool_update_application_status(
            {"application_name": app_a.name, "new_status": word})
    assert result.get("success"), result
    assert _stored(db_session, orgs["org_a"], [app_a.name]) == {app_a.name: expected}


def test_the_chat_tool_still_refuses_an_unknown_status(orgs, db_session, tenant_ctx):
    from app.modules.ai_chat.tools.executor import ToolExecutor

    app_a = _make_app(db_session, orgs["org_a"], "Ledger")
    executor = ToolExecutor.__new__(ToolExecutor)
    result = executor._tool_update_application_status(
        {"application_name": app_a.name, "new_status": "Mystery"})
    assert result["success"] is False
    assert "Status 'Mystery' is not one of: design, development" in result["error"]


def test_the_planned_figure_counts_design_in_any_case_and_old_planned(orgs, db_session,
                                                                      tenant_ctx):
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.metrics import ApplicationMetricsSnapshot

    for word in ("design", "Design ", "planned", "not_started", "development", "production"):
        row = _make_app(db_session, orgs["org_a"], "Ledger")
        db_session.flush()
        # Rows stored before the list existed are seeded as stored: the model refuses them.
        db_session.execute(
            db.text("UPDATE application_components SET deployment_status = :w WHERE id = :i"),
            {"w": word, "i": row.id},
        )
    db_session.expire_all()
    with tenant_ctx(orgs["org_a"].id):
        snapshot = ApplicationMetricsSnapshot.create_snapshot()
        rows = ApplicationComponent.query.all()
        planned_rows = [r for r in rows if (r.deployment_status or "").strip().lower()
                        in ("design", "planned", "not_started")]
    assert snapshot.planned_count == len(planned_rows) == 4


def test_a_committed_batch_job_stores_the_status_the_preview_mapped(orgs, db_session, tenant_ctx):
    from app.models.batch_import import BatchImportApplication
    from app.modules.import_batch.services.batch_approval_service import BatchApprovalService
    from app.services.import_validation.import_validator import ImportValidator

    names = _names()
    committed = {}
    with tenant_ctx(orgs["org_a"].id):
        service = BatchApprovalService()
        for name, (word, expected) in zip(names, ROWS):
            staged = BatchImportApplication(
                row_number=1, application_name=name,
                source_data={"name": name, "deployment_status": word},
            )
            made = service._commit_application(staged)
            committed[name] = made.deployment_status
            previewed = ImportValidator(mode="lenient").validate(
                [{"name": name, "deployment_status": word}]
            ).row_results[0].normalized_data.get("deployment_status")
            if expected != "REFUSED":
                assert made.deployment_status == previewed, (word, made.deployment_status, previewed)
    assert committed[names[0]] == "production"
    assert committed[names[1]] == "design"
    assert committed[names[2]] == "retiring"
    assert committed[names[3]] is None
    assert committed[names[4]] is None, "a word the preview cannot map is not guessed"
