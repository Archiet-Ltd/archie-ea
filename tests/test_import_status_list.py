"""The batch import reads the one list of application statuses.

A status the Overview offers (design, retiring) must pass the import preview;
older words (planned, deprecated, retired) map to a value on the list; and a
status is matched without regard to case or surrounding spaces. Two
organisations throughout: a second organisation cannot read the first's preview.
"""

from __future__ import annotations

import uuid

import pytest

from tests.test_application_overview_save import _make_user

pytestmark = pytest.mark.usefixtures("db_session")


def _validate(mode, status):
    from app.services.import_validation.import_validator import ImportValidator

    result = ImportValidator(mode=mode).validate(
        [{"name": "Ledger " + uuid.uuid4().hex[:6], "deployment_status": status}]
    )
    row = result.row_results[0]
    return result, row


@pytest.mark.parametrize("status", ["design", "retiring", "Design", "  RETIRING  "])
def test_a_status_on_the_list_passes_the_strict_import(status):
    from app.services.application_status import APPLICATION_STATUS_VALUES

    result, row = _validate("strict", status)
    assert row.is_valid, [i.message for i in row.issues]
    assert row.normalized_data["deployment_status"] == status.strip().lower()
    assert row.normalized_data["deployment_status"] in APPLICATION_STATUS_VALUES
    assert not [i for i in row.issues if i.field_name == "deployment_status"], row.issues


@pytest.mark.parametrize(
    "older, expected",
    [("planned", "design"), ("deprecated", "retiring"), ("retired", "decommissioned"),
     ("Decommissioned", "decommissioned"), (" EOL ", "decommissioned"), ("live", "production")],
)
def test_an_older_word_maps_to_a_value_on_the_list(older, expected):
    result, row = _validate("strict", older)
    assert row.is_valid, [i.message for i in row.issues]
    assert row.normalized_data["deployment_status"] == expected


def test_the_import_allows_exactly_the_one_list_and_its_older_words():
    from app.services.application_status import APPLICATION_STATUS_VALUES, OLD_STATUS_WORDS
    from app.services.import_validation import validation_schemas as v1
    from app.modules.import_batch.v2.services.import_validation import validation_schemas_v2 as v2

    for schemas in (v1, v2):
        assert list(schemas.DEPLOYMENT_STATUS_VALUES) == list(APPLICATION_STATUS_VALUES)
        for word, target in OLD_STATUS_WORDS.items():
            assert schemas.DEPLOYMENT_STATUS_ALIASES[word] == target
        assert set(schemas.DEPLOYMENT_STATUS_ALIASES.values()) <= set(APPLICATION_STATUS_VALUES)


def test_an_application_retiring_with_a_retirement_date_is_not_warned():
    from app.services.import_validation.import_validator import ImportValidator

    rows = [{"name": "Ledger one", "deployment_status": "Retiring ", "retirement_date": "2030-01-01"}]
    result = ImportValidator(mode="lenient").validate(rows)
    issues = result.row_results[0].issues
    assert not [i for i in issues if i.field_name == "retirement_date"], issues


def test_a_design_status_or_an_older_word_for_it_is_matched_to_the_list():
    from app.services.application_status import canonical_status

    assert canonical_status("Design ") == "design"
    assert canonical_status("planned") == "design"
    assert canonical_status("production") == "production"
    assert canonical_status("nonsense") is None


def test_the_import_preview_accepts_design_and_retiring_and_is_private(
    client, login_as, db_session, make_org
):
    from app.models.batch_import import (
        BatchImportApplication, BatchImportBatch, BatchImportJob, BatchJobStatus,
    )

    org_a, org_b = make_org("imp-a"), make_org("imp-b")
    user_a = _make_user(db_session, org_a, "impa")
    user_b = _make_user(db_session, org_b, "impb")
    job = BatchImportJob(
        user_id=user_a.id, filename="portfolio.csv", total_applications=3, total_batches=1,
        status=BatchJobStatus.AWAITING_CONFIRMATION,
    )
    db_session.add(job)
    db_session.flush()
    batch = BatchImportBatch(job_id=job.id, batch_number=1, total_applications=3)
    db_session.add(batch)
    db_session.flush()
    for number, status in enumerate(["design", "retiring", "decommissioned"], start=1):
        db_session.add(BatchImportApplication(
            batch_id=batch.id, row_number=number, application_name=f"Ledger {number}",
            source_data={"name": f"Ledger {number}", "deployment_status": status},
        ))
    db_session.flush()

    login_as(client, user_a)
    resp = client.get(f"/api/batch-import/jobs/{job.id}/preview")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    validation = resp.get_json()["data"]["validation"]
    assert validation["summary"]["total_rows"] == 3
    assert validation["summary"]["total_errors"] == 0, validation
    assert validation["summary"]["total_warnings"] == 0, validation
    assert "deployment_status" not in validation["warnings_by_field"]
    assert "deployment_status" not in validation["errors_by_field"]

    login_as(client, user_b)
    assert client.get(f"/api/batch-import/jobs/{job.id}/preview").status_code == 404
