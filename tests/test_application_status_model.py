"""The application model refuses any status that is not on the one list.

Every writer reaches the deployment-status column through the model, so one
validator keeps the list whole: a value on the list, an older word or a synonym is
stored in the list's form, a blank stores nothing, a value equal to the one already
stored passes, and anything else raises one named error carrying the one refusal
sentence. Each writer reports that sentence in its own way. Two organisations
throughout: nothing a writer stores appears in the other organisation.
"""

from __future__ import annotations

import asyncio
import html as htmllib
import io
import json
import re
import uuid

import pytest

from tests.test_application_overview_save import (
    FORM_FIELDS,
    _csrf,
    _edit_page,
    _form_action,
    _make_app,
    _make_user,
    _store_as_stored,
)

pytestmark = pytest.mark.usefixtures("db_session")

BANANA = "banana"


def _sentence(word=BANANA):
    from app.services.application_status import refusal_for

    return refusal_for(word)


@pytest.fixture
def orgs(db_session, make_org):
    org_a, org_b = make_org("stm-a"), make_org("stm-b")
    return {
        "org_a": org_a, "org_b": org_b,
        "user_a": _make_user(db_session, org_a, "usera"),
        "user_b": _make_user(db_session, org_b, "userb"),
        "owner_a": _make_user(db_session, org_a, "ownera", persona="application_manager"),
    }


def _stored(db_session, org, names):
    from app.models.application_portfolio import ApplicationComponent

    db_session.expire_all()
    rows = ApplicationComponent.query.filter(
        ApplicationComponent.organization_id == org.id,
        ApplicationComponent.name.in_(names),
    ).all()
    return {row.name: row.deployment_status for row in rows}


def _tag():
    return uuid.uuid4().hex[:6]


# --- the model ---------------------------------------------------------------


@pytest.mark.parametrize(
    "word, stored",
    [("production", "production"), ("Production ", "production"), ("Live", "production"),
     ("planned", "design"), ("Retired", "decommissioned"), ("", None), (None, None)],
)
def test_the_model_stores_a_value_in_the_lists_form(orgs, db_session, word, stored):
    row = _make_app(db_session, orgs["org_a"], "Model")
    row.deployment_status = word
    db_session.flush()
    assert row.deployment_status == stored


def test_the_model_refuses_a_word_off_the_list_with_the_one_sentence(orgs, db_session):
    from app.models.application_portfolio import ApplicationComponent
    from app.services.application_status import StatusRefused

    row = _make_app(db_session, orgs["org_a"], "Model")
    with pytest.raises(StatusRefused) as refused:
        row.deployment_status = BANANA
    assert str(refused.value) == _sentence()
    assert row.deployment_status == "development"
    with pytest.raises(StatusRefused):
        ApplicationComponent(name="New " + _tag(), organization_id=orgs["org_a"].id,
                             deployment_status=BANANA)


def test_the_model_keeps_a_stored_word_off_the_list_until_another_is_picked(orgs, db_session):
    from app.services.application_status import StatusRefused

    row = _make_app(db_session, orgs["org_a"], "Model")
    _store_as_stored(db_session, row, "Mystery")
    row.deployment_status = "mystery "
    db_session.flush()
    assert row.deployment_status == "mystery "
    with pytest.raises(StatusRefused):
        row.deployment_status = BANANA
    row.deployment_status = "staging"
    assert row.deployment_status == "staging"


# --- Abacus -------------------------------------------------------------------


def _abacus_values():
    from app.config import abacus_field_mapping as mapping
    from app.modules.applications.services.abacus_application_importer import (
        AbacusComponentTransformer,
    )

    values = set(mapping.LIFECYCLE_TO_DEPLOYMENT.values())
    values |= {mapping.normalize_lifecycle_status(key) for key in mapping.LIFECYCLE_TO_DEPLOYMENT}
    values |= {mapping.derive_deployment_from_lifecycle(key)
               for key in mapping.LIFECYCLE_TO_DEPLOYMENT}
    values |= set(AbacusComponentTransformer.STATUS_MAPPING.values())
    return sorted(values)


@pytest.mark.parametrize("value", _abacus_values())
def test_each_value_abacus_derives_maps_to_a_value_on_the_list(value):
    from app.services.application_status import APPLICATION_STATUS_VALUES, status_for_write

    stored, refusal = status_for_write(value)
    assert refusal is None, refusal
    assert stored in APPLICATION_STATUS_VALUES


def test_abacus_words_are_mapped_by_the_one_synonym_table_not_in_the_abacus_module():
    import inspect

    from app.config import abacus_field_mapping

    assert "STATUS_SYNONYMS" not in inspect.getsource(abacus_field_mapping)


def test_an_abacus_import_stores_the_list_value_and_refuses_the_unknown(orgs, db_session,
                                                                         tenant_ctx):
    from app.modules.import_batch.services.abacus_import_service import AbacusImportService

    tag = _tag()
    rows = [
        {"external_id": f"{tag}-1", "name": f"Abacus One {tag}", "deployment_status": "operational"},
        {"external_id": f"{tag}-2", "name": f"Abacus Two {tag}", "deployment_status": "planning"},
        {"external_id": f"{tag}-3", "name": f"Abacus Bad {tag}", "deployment_status": BANANA},
        {"external_id": f"{tag}-4", "name": f"Abacus Four {tag}", "deployment_status": "retired"},
    ]
    with tenant_ctx(orgs["org_a"].id):
        service = AbacusImportService.__new__(AbacusImportService)
        service.refusals = []
        service.stats = {"applications_created": 0, "applications_updated": 0,
                         "applications_skipped": 0, "applications_errors": 0}
        asyncio.run(service.import_applications(rows))
    names = [row["name"] for row in rows]
    stored = _stored(db_session, orgs["org_a"], names)
    assert stored == {names[0]: "production", names[1]: "design", names[3]: "decommissioned"}
    assert service.stats["applications_errors"] == 1
    assert service.refusals == [{"name": names[2], "reason": _sentence()}]
    assert _stored(db_session, orgs["org_b"], names) == {}


# --- the writers: banana is refused with the one sentence, nothing stored --------


def _file_post(client, filename, payload, accept=None):
    headers = {"Accept": accept} if accept else {}
    return client.post(
        "/applications/import",
        data={"file": (io.BytesIO(payload), filename), "import_mode": "skip"},
        content_type="multipart/form-data", headers=headers,
    )


def _xlsx(headers, rows):
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _three_rows(tag):
    """The review's three rows: one good, one refused, one good."""
    return [(f"Alpha {tag}", "design"), (f"Bravo {tag}", "Mystery"), (f"Charlie {tag}", "Live")]


def _payload(kind, rows):
    if kind == "csv":
        text = "Name,Status\n" + "".join(f"{name},{status}\n" for name, status in rows)
        return "apps.csv", text.encode()
    if kind == "xlsx":
        return "apps.xlsx", _xlsx(["Name", "Status"], [list(row) for row in rows])
    return "apps.json", json.dumps(
        [{"name": name, "deployment_status": status} for name, status in rows]).encode()


@pytest.mark.parametrize("kind", ["csv", "xlsx", "json"])
def test_each_file_type_stores_two_of_three_rows_and_says_why_the_third_was_refused(
    orgs, client, login_as, db_session, kind
):
    tag = _tag()
    rows = _three_rows(tag)
    filename, payload = _payload(kind, rows)
    login_as(client, orgs["user_a"])
    resp = _file_post(client, filename, payload, accept="application/json")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["stored"] == 2
    assert [(r["row"], r["name"], r["reason"]) for r in body["refused"]] == [
        (3 if kind != "json" else 2, rows[1][0], _sentence("Mystery"))]
    stored = _stored(db_session, orgs["org_a"], [name for name, _ in rows])
    assert stored == {rows[0][0]: "design", rows[2][0]: "production"}
    assert _stored(db_session, orgs["org_b"], [name for name, _ in rows]) == {}


@pytest.mark.parametrize("kind", ["csv", "xlsx", "json"])
def test_a_plain_form_post_keeps_its_flash_and_redirect(orgs, client, login_as, db_session, kind):
    tag = _tag()
    rows = _three_rows(tag)
    filename, payload = _payload(kind, rows)
    login_as(client, orgs["user_a"])
    resp = _file_post(client, filename, payload)
    assert resp.status_code == 302
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Import complete: 2 created, 1 refused" in page.replace("\\u0027", "'")


def test_a_json_file_may_be_wrapped_and_its_columns_mapped(orgs, client, login_as, db_session):
    tag = _tag()
    payload = json.dumps({"applications": [
        {"App": f"Wrapped {tag}", "Lifecycle": "Live"}]}).encode()
    login_as(client, orgs["user_a"])
    resp = client.post(
        "/applications/import",
        data={"file": (io.BytesIO(payload), "apps.json"), "import_mode": "skip",
              "field_mappings": json.dumps({"App": "name", "Lifecycle": "deployment_status"})},
        content_type="multipart/form-data", headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["stored"] == 1
    assert _stored(db_session, orgs["org_a"], [f"Wrapped {tag}"]) == {f"Wrapped {tag}": "production"}


@pytest.mark.parametrize("kind", ["csv", "xlsx", "json"])
def test_banana_through_each_file_import_is_refused_and_not_stored(orgs, client, login_as,
                                                                    db_session, kind):
    name = f"Banana {_tag()}"
    filename, payload = _payload(kind, [(name, BANANA)])
    login_as(client, orgs["user_a"])
    resp = _file_post(client, filename, payload, accept="application/json")
    body = resp.get_json()
    assert body["stored"] == 0
    assert body["refused"][0]["reason"] == _sentence()
    assert _stored(db_session, orgs["org_a"], [name]) == {}


def test_banana_through_the_upload_route_is_refused_and_not_stored(orgs, client, login_as,
                                                                   db_session):
    tag = _tag()
    names = [f"Up A {tag}", f"Up B {tag}"]
    csv = f"Application Name,Application Status\n{names[0]},Live\n{names[1]},{BANANA}\n"
    login_as(client, orgs["user_a"])
    resp = client.post(
        "/applications/upload-excel",
        data={"file": (io.BytesIO(csv.encode()), "up.csv"), "duplicate_mode": "merge",
              "enable_ai": "false",
              "field_mappings": json.dumps({"Application Name": "name",
                                            "Application Status": "deployment_status"})},
        content_type="multipart/form-data",
    )
    assert resp.get_json()["errors"] == [f"Row 3: {_sentence()}"]
    assert _stored(db_session, orgs["org_a"], names) == {names[0]: "production"}
    assert _stored(db_session, orgs["org_b"], names) == {}


def test_banana_through_the_chat_update_is_refused_and_not_stored(orgs, db_session, tenant_ctx):
    from app.modules.ai_chat.services.ai_data_interaction_service import AIDataInteractionService

    row = _make_app(db_session, orgs["org_a"], "Chat")
    with tenant_ctx(orgs["org_a"].id):
        result = AIDataInteractionService().update_application(
            row.id, {"deployment_status": BANANA})
    assert result["success"] is False
    assert result["error"] == _sentence()
    assert _stored(db_session, orgs["org_a"], [row.name]) == {row.name: "development"}


def test_banana_through_the_chat_status_tool_is_refused_and_not_stored(orgs, db_session,
                                                                        tenant_ctx):
    from app.modules.ai_chat.tools.executor import ToolExecutor

    row = _make_app(db_session, orgs["org_a"], "Chat")
    executor = ToolExecutor.__new__(ToolExecutor)
    result = executor._tool_update_application_status(
        {"application_name": row.name, "new_status": BANANA})
    assert result == {"success": False, "error": _sentence()}
    assert _stored(db_session, orgs["org_a"], [row.name]) == {row.name: "development"}


def test_banana_through_the_overview_form_is_refused_on_the_field(orgs, client, login_as,
                                                                  db_session):
    row = _make_app(db_session, orgs["org_a"], "Overview")
    login_as(client, orgs["user_a"])
    html = _edit_page(client, row.id)
    resp = client.post(
        _form_action(html),
        data=dict(FORM_FIELDS, csrf_token=_csrf(html), deployment_status=BANANA),
    )
    assert resp.status_code == 422
    assert _sentence() in htmllib.unescape(resp.get_data(as_text=True))
    assert _stored(db_session, orgs["org_a"], [row.name]) == {row.name: "development"}


def test_banana_through_the_create_api_is_refused_with_the_sentence(orgs, client, login_as,
                                                                    db_session):
    name = f"Created {_tag()}"
    login_as(client, orgs["user_a"])
    resp = client.post("/applications/create",
                       json={"name": name, "deployment_status": BANANA})
    assert resp.status_code >= 400
    assert _sentence() in resp.get_data(as_text=True)
    assert _stored(db_session, orgs["org_a"], [name]) == {}


def test_banana_through_the_manual_import_is_refused_with_the_sentence(orgs, client, login_as,
                                                                        db_session):
    name = f"Manual {_tag()}"
    login_as(client, orgs["user_a"])
    resp = client.post(
        "/applications/import-manual",
        json={"applications": [{"name": name, "deployment_status": BANANA}],
              "duplicate_mode": "skip"},
    )
    assert resp.status_code == 400
    assert _sentence() in resp.get_json()["error"]
    assert _stored(db_session, orgs["org_a"], [name]) == {}


def test_banana_through_the_staged_commit_refuses_that_row_and_keeps_the_rest(
    orgs, db_session, tenant_ctx
):
    from app.models.import_session import StagingElementType
    from app.modules.import_batch.services.import_staging_service import ImportStagingService

    service = ImportStagingService.__new__(ImportStagingService)
    service.logger = __import__("logging").getLogger("test")
    tag = _tag()
    committed = []

    class Row:
        def __init__(self, status):
            self.element_type = StagingElementType.APPLICATION.value
            self.element_uuid = uuid.uuid4().hex
            self.element_data = {"name": f"Staged {status} {tag}",
                                 "organization_id": orgs["org_a"].id,
                                 "deployment_status": status}
            self.requires_review = False
            self.element_metadata = None

    with tenant_ctx(orgs["org_a"].id):
        good, bad = Row("production"), Row(BANANA)
        for element in (bad, good):
            try:
                committed.append(service._commit_element_by_type(element))
            except Exception as exc:  # the named error is what commit_staged_elements catches
                assert str(exc) == _sentence()
    assert len(committed) == 1
    assert _stored(db_session, orgs["org_a"], [good.element_data["name"], bad.element_data["name"]]) \
        == {good.element_data["name"]: "production"}


# --- the batch approval merge --------------------------------------------------


def _staged(db_session, user, source, name):
    from app.models.batch_import import (
        BatchImportApplication, BatchImportBatch, BatchImportJob, BatchJobStatus,
    )

    job = BatchImportJob(user_id=user.id, filename="portfolio.csv", total_applications=1,
                         total_batches=1, status=BatchJobStatus.AWAITING_CONFIRMATION)
    db_session.add(job)
    db_session.flush()
    batch = BatchImportBatch(job_id=job.id, batch_number=1, total_applications=1)
    db_session.add(batch)
    db_session.flush()
    staged = BatchImportApplication(batch_id=batch.id, row_number=4, application_name=name,
                                    source_data=source)
    db_session.add(staged)
    db_session.flush()
    return job, batch, staged


@pytest.mark.parametrize("heading", ["deployment_status", "Status", "Application Status",
                                     "Deployment Status", "App Status"])
def test_a_batch_row_matching_an_existing_application_merges_its_mapped_status(
    orgs, db_session, tenant_ctx, heading
):
    from app.modules.import_batch.services.batch_approval_service import BatchApprovalService

    existing = _make_app(db_session, orgs["org_a"], "Merge")
    _job, _batch, staged = _staged(db_session, orgs["user_a"],
                                   {"name": existing.name, heading: "Live"}, existing.name)
    with tenant_ctx(orgs["org_a"].id):
        made = BatchApprovalService()._commit_application(staged)
        db_session.flush()
    assert made.id == existing.id
    assert _stored(db_session, orgs["org_a"], [existing.name]) == {existing.name: "production"}


def test_a_batch_row_with_banana_leaves_the_stored_status_and_records_the_sentence(
    orgs, db_session, tenant_ctx
):
    from app.modules.import_batch.services.batch_approval_service import BatchApprovalService

    existing = _make_app(db_session, orgs["org_a"], "Merge")
    _job, _batch, staged = _staged(db_session, orgs["user_a"],
                                   {"name": existing.name, "Status": BANANA}, existing.name)
    new_name = f"Fresh {_tag()}"
    _job2, _batch2, fresh = _staged(db_session, orgs["user_a"],
                                    {"name": new_name, "Application Status": BANANA}, new_name)
    with tenant_ctx(orgs["org_a"].id):
        service = BatchApprovalService()
        service._commit_application(staged)
        service._commit_application(fresh)
        db_session.flush()
    assert _stored(db_session, orgs["org_a"], [existing.name, new_name]) == {
        existing.name: "development", new_name: None}
    assert [r["reason"] for r in service.status_refusals] == [_sentence(), _sentence()]


def test_the_batch_preview_reads_a_status_column_under_any_heading(orgs, client, login_as,
                                                                    db_session):
    tag = _tag()
    job, _batch, _staged_row = _staged(
        db_session, orgs["user_a"],
        {"name": f"Preview {tag}", "Application Status": "Mystery"}, f"Preview {tag}")
    login_as(client, orgs["user_a"])
    resp = client.get(f"/api/batch-import/jobs/{job.id}/preview")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    validation = resp.get_json()["data"]["validation"]
    issues = {**validation["errors_by_field"], **validation["warnings_by_field"]}
    assert "deployment_status" in issues, validation


# --- the owner's edit page -------------------------------------------------------


def _owned(db_session, org, user, stored_word):
    from app.models.application_owner import ApplicationOwner

    row = _make_app(db_session, org, "Owned")
    _store_as_stored(db_session, row, stored_word)
    db_session.add(ApplicationOwner(application_id=row.id, user_id=user.id,
                                    organization_id=org.id))
    db_session.flush()
    return row


def _chosen(html):
    assert '<select id="deployment_status"' in html, html[:400]
    block = re.search(r'<select id="deployment_status".*?</select>', html, re.S).group(0)
    options = re.findall(r'<option value="([^"]*)"\s*(selected)?', block)
    return [value for value, selected in options if selected], [value for value, _ in options]


@pytest.mark.parametrize("stored", ["staging", "retiring", "Production", "design"])
def test_the_owner_edit_page_opens_on_the_stored_status_and_keeps_it_on_save(
    orgs, client, login_as, db_session, stored
):
    from app.services.application_status import APPLICATION_STATUS_VALUES

    row = _owned(db_session, orgs["org_a"], orgs["owner_a"], stored)
    login_as(client, orgs["owner_a"])
    url = f"/my-applications/app/{row.id}/edit"
    first = client.get(url)
    assert first.status_code == 200, (first.status_code, re.findall(r"<title>(.*?)</title>", first.get_data(as_text=True), re.S))
    html = first.get_data(as_text=True)
    chosen, offered = _chosen(html)
    assert chosen == [stored.lower()], (chosen, offered)
    assert [v for v in offered if v] == list(APPLICATION_STATUS_VALUES)
    resp = client.post(url, data={"deployment_status": chosen[0], "business_owner": "Ada",
                                  "technical_owner": "Tom"})
    assert resp.status_code == 302
    after = _stored(db_session, orgs["org_a"], [row.name])[row.name]
    assert after is not None and after.lower() == stored.lower()


def test_the_owner_edit_page_shows_a_stored_word_off_the_list_as_itself_and_keeps_it(
    orgs, client, login_as, db_session
):
    row = _owned(db_session, orgs["org_a"], orgs["owner_a"], "Mystery")
    login_as(client, orgs["owner_a"])
    url = f"/my-applications/app/{row.id}/edit"
    chosen, offered = _chosen(client.get(url).get_data(as_text=True))
    assert chosen == ["Mystery"] and offered[-1] == "Mystery"
    client.post(url, data={"deployment_status": "Mystery"})
    assert _stored(db_session, orgs["org_a"], [row.name]) == {row.name: "Mystery"}


def test_banana_through_the_owner_edit_form_is_refused_on_the_field(orgs, client, login_as,
                                                                    db_session):
    row = _owned(db_session, orgs["org_a"], orgs["owner_a"], "staging")
    login_as(client, orgs["owner_a"])
    resp = client.post(f"/my-applications/app/{row.id}/edit",
                       data={"deployment_status": BANANA, "description": "Changed"})
    assert resp.status_code == 400
    assert _sentence() in htmllib.unescape(resp.get_data(as_text=True))
    assert _stored(db_session, orgs["org_a"], [row.name]) == {row.name: "staging"}
    assert _stored(db_session, orgs["org_b"], [row.name]) == {}


def test_no_second_list_of_deployment_statuses_remains_in_the_owner_module():
    import app.modules.my_applications.crud_routes as owner_routes

    assert not hasattr(owner_routes, "DEPLOYMENT_STATUSES")


# --- another organisation's job and batch answer not found -----------------------


def test_another_organisations_job_and_batch_answer_not_found(orgs, client, login_as,
                                                              db_session):
    job, batch, _row = _staged(db_session, orgs["user_a"],
                               {"name": "Secret ledger", "Status": "Live"}, "Secret ledger")
    login_as(client, orgs["user_b"])
    review = client.get(f"/batch-import/batches/{batch.id}/review", follow_redirects=False)
    assert review.status_code == 404
    assert client.post(f"/api/batch-import/batches/{batch.id}/commit").status_code == 404
    assert client.get(f"/api/batch-import/jobs/{job.id}/preview").status_code == 404
    stream = client.get(f"/api/batch-import/jobs/{job.id}/stream", buffered=True)
    assert stream.status_code == 404
    assert "Secret ledger" not in stream.get_data(as_text=True)
    assert "event:" not in stream.get_data(as_text=True)

    login_as(client, orgs["user_a"])
    assert client.get(f"/batch-import/batches/{batch.id}/review").status_code == 200
