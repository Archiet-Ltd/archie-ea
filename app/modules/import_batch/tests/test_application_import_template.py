"""The import page offers an application template whose header the importer reads."""

from __future__ import annotations

import csv
import io
import uuid
from pathlib import Path

from app.modules.import_batch.services.import_orchestrator import ImportOrchestrator

TEMPLATE = Path(__file__).resolve().parents[4] / "app" / "static" / "templates" / "application_import_template.csv"


def _rows():
    with TEMPLATE.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_every_template_column_is_read_by_the_importers_own_mapping():
    rows = _rows()
    assert len(rows) >= 2
    importer = ImportOrchestrator()
    header = list(rows[0].keys())
    assert header[0] == "name"
    for column in header:
        context = importer._build_app_context({column: "x"}, [column])
        read = {key: value for key, value in context.items() if key != "source_data"}
        assert "x" in read.values(), f"{column} is not read by the importer"


def test_the_import_page_links_the_template_and_lists_its_columns(
    app, db_session, make_org, client, login_as
):
    from app.models.user import User

    org = make_org("tpl-page")
    user = User(
        email=f"tpl-{uuid.uuid4().hex[:8]}@example.com", first_name="T", last_name="P",
        organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    login_as(client, user)
    response = client.get("/batch-import/new")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "/static/templates/application_import_template.csv" in html
    for column in _rows()[0]:
        assert f"<strong class=\"text-foreground\">{column}</strong>" in html
    assert TEMPLATE.exists()


def test_the_template_imports_two_applications_through_the_importer(app, db_session, make_org):
    """The page posts to the batch import service; two rows become two applications."""
    from werkzeug.datastructures import FileStorage

    from app.models.user import User
    from app.modules.import_batch.services.batch_import_service import BatchImportService

    org = make_org("tpl-import")
    user = User(
        email=f"tpl-{uuid.uuid4().hex[:8]}@example.com", first_name="T", last_name="I",
        organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    upload = FileStorage(
        stream=io.BytesIO(TEMPLATE.read_bytes()), filename="application_import_template.csv",
        content_type="text/csv",
    )
    job = BatchImportService().create_job(user.id, upload, enable_ai_generation=False)
    rows = [a for batch in job.batches for a in batch.applications]
    print("IMPORT RESULT:", job.total_applications, job.status, [
        (r.application_name, r.application_description, r.application_type, r.vendor_name) for r in rows])
    assert job.total_applications == 2
    assert sorted(r.application_name for r in rows) == ["Customer Portal", "Payroll"]
    assert all(r.application_type and r.vendor_name and r.application_description for r in rows)
