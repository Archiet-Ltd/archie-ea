"""Playwright journey test for batch import with NULL counters.

A Data Architect views a batch import job whose counters are NULL (legacy/migration data).
The job detail page must render with content, not error, and show 0% progress.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD


pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    page.wait_for_timeout(800)
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def _create_job_with_null_counters(app, user_id, org_id):
    """Create a batch import job with NULL counters in the live database."""
    from app import db
    from app.models.batch_import import BatchImportJob, BatchImportBatch, BatchJobStatus, BatchStatus
    from sqlalchemy import text

    with app.app_context():
        job = BatchImportJob(
            job_uuid="null-counters-journey-%s" % uuid.uuid4().hex[:8],
            user_id=user_id,
            filename="legacy_import.csv",
            total_applications=25,
            batch_size=10,
            total_batches=3,
            status=BatchJobStatus.PENDING,
        )
        db.session.add(job)
        db.session.flush()

        # Create batches
        for i in range(3):
            batch = BatchImportBatch(
                job_id=job.id,
                batch_number=i + 1,
                status=BatchStatus.QUEUED,
                total_applications=10 if i < 2 else 5,
            )
            db.session.add(batch)
        db.session.commit()
        job_id = job.id

        # Set all counters to NULL via raw SQL (simulating legacy/migration data)
        db.session.execute(
            text(
                """
            UPDATE batch_import_job
            SET batches_completed = NULL,
                batches_approved = NULL,
                batches_rejected = NULL,
                batches_committed = NULL
            WHERE id = :id
            """
            ),
            {"id": job_id},
        )
        db.session.execute(
            text(
                """
            UPDATE batch_import_batch
            SET processed_applications = NULL,
                total_elements_generated = NULL,
                elements_approved = NULL,
                elements_rejected = NULL
            WHERE job_id = :job_id
            """
            ),
            {"job_id": job_id},
        )
        db.session.commit()

        return job_id


def test_job_detail_with_null_counters_renders(browser, live_server, seeded, app):
    """
    Journey: A Data Architect opens a batch import job detail page where the
    job's counters are NULL (simulating legacy/migration data).

    The page must:
    - Return HTTP 200 (not 500)
    - Render the job detail content (not an error page)
    - Show 0% progress (not NaN or error)
    - Show the job name and filename
    """
    from app import db
    from app.models.user import User

    with app.app_context():
        email = seeded["emails"]["data_architect"]
        user = db.session.query(User).filter_by(email=email).first()
        assert user is not None, "seeded data_architect user not found"
        user_id = user.id

    # Create job with NULL counters in the live database
    job_id = _create_job_with_null_counters(app, user_id, seeded["ids"]["org"])

    # Now test the journey
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()

    _login(page, live_server, email)

    # Navigate directly to the job detail page
    page.goto(live_server + "/batch-import/jobs/%d" % job_id, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(2000)

    # Page should render successfully (not 500 error, not redirect to dashboard)
    assert "/batch-import/jobs/%d" % job_id in page.url, "Redirected to %s" % page.url

    # Check page title contains job identifier
    assert "Import Job:" in page.title()
    assert "legacy_import.csv" in page.title()

    # Check main content renders
    h1_text = page.locator("h1").first.inner_text()
    assert "legacy_import.csv" in h1_text

    # Check progress shows 0% (not NaN or error)
    progress_text = page.locator("text=0%").first
    expect(progress_text).to_be_visible(timeout=PAGE_TIMEOUT)

    # Check no error banners
    error_banner = page.locator("text=Invalid input provided")
    expect(error_banner).not_to_be_visible()

    context.close()


def _create_api_job_with_null_counters(app, user_id, org_id):
    """Create a batch import job with NULL counters for API test."""
    from app import db
    from app.models.batch_import import BatchImportJob, BatchImportBatch, BatchJobStatus, BatchStatus
    from sqlalchemy import text

    with app.app_context():
        job = BatchImportJob(
            job_uuid="null-counters-api-%s" % uuid.uuid4().hex[:8],
            user_id=user_id,
            filename="api_test.csv",
            total_applications=10,
            batch_size=20,
            total_batches=1,
            status=BatchJobStatus.PENDING,
        )
        db.session.add(job)
        db.session.flush()

        batch = BatchImportBatch(
            job_id=job.id,
            batch_number=1,
            status=BatchStatus.QUEUED,
            total_applications=10,
        )
        db.session.add(batch)
        db.session.commit()
        job_id = job.id

        # Set counters to NULL
        db.session.execute(
            text(
                """
            UPDATE batch_import_job
            SET batches_completed = NULL,
                batches_approved = NULL,
                batches_rejected = NULL,
                batches_committed = NULL
            WHERE id = :id
            """
            ),
            {"id": job_id},
        )
        db.session.execute(
            text(
                """
            UPDATE batch_import_batch
            SET processed_applications = NULL,
                total_elements_generated = NULL,
                elements_approved = NULL,
                elements_rejected = NULL
            WHERE job_id = :job_id
            """
            ),
            {"job_id": job_id},
        )
        db.session.commit()

        return job_id


def test_api_jobs_list_with_null_counters_returns_200(browser, live_server, seeded, app):
    """
    Journey: A Data Architect loads the batch import dashboard where a job
    has NULL counters.

    The API /api/batch-import/jobs must return 200 with valid JSON.
    """
    from app import db
    from app.models.user import User

    with app.app_context():
        email = seeded["emails"]["data_architect"]
        user = db.session.query(User).filter_by(email=email).first()
        assert user is not None
        user_id = user.id

    # Create job with NULL counters in the live database
    job_id = _create_api_job_with_null_counters(app, user_id, seeded["ids"]["org"])

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()

    _login(page, live_server, email)

    # Intercept the API call
    api_response = None

    def handle_response(response):
        nonlocal api_response
        if "/api/batch-import/jobs" in response.url and response.request.method == "GET":
            api_response = response

    page.on("response", handle_response)

    # Navigate to dashboard
    page.goto(live_server + "/batch-import/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(2000)

    # Wait for API response
    page.wait_for_timeout(1000)

    assert api_response is not None, "API call to /api/batch-import/jobs not captured"
    assert api_response.status == 200, "API returned %d: %s" % (api_response.status, api_response.text())

    # Parse JSON and verify structure
    data = api_response.json()
    assert data.get("success") is True
    jobs = data.get("jobs", [])
    assert len(jobs) >= 1

    # Find our job
    our_job = next((j for j in jobs if j.get("id") == job_id), None)
    assert our_job is not None, "Our job not found in API response"

    # Verify progress is 0 (not error)
    assert our_job.get("progress") == 0.0
    assert our_job.get("progress_percentage") == 0.0
    assert our_job.get("batches_completed") == 0

    context.close()