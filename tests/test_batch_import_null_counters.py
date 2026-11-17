"""Test that batch import handles NULL counters gracefully.

A batch_import_job whose nullable integer counters are NULL must not cause
GET /api/batch-import/jobs or the job detail page to fail with
"TypeError: unsupported operand type(s) for /: 'NoneType' and 'int'".
Empty counters count as 0 in progress and rate calculations.
"""

import pytest


@pytest.mark.usefixtures("db_session")
def test_job_with_null_counters_api_returns_200(app, db_session, make_org, tenant_ctx):
    """GET /api/batch-import/jobs returns 200 for a job with NULL counters."""
    from app.models.user import User
    from app.models.batch_import import BatchImportJob, BatchJobStatus
    from sqlalchemy import text

    org = make_org("null-counters-api")
    with tenant_ctx(org.id):
        user = User(
            email=f"nullcounters-{org.id}@example.com",
            organization_id=org.id,
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        db_session.add(user)
        db_session.commit()

        # Create job through ORM
        job = BatchImportJob(
            job_uuid="null-counters-job",
            user_id=user.id,
            filename="test.csv",
            total_applications=10,
            batch_size=20,
            total_batches=1,
            status=BatchJobStatus.PENDING,
        )
        db_session.add(job)
        db_session.commit()
        job_id = job.id

        # Set counters to NULL via raw SQL (simulating legacy/migration data)
        db_session.execute(
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
        db_session.commit()

        # Login and call the API
        from tests.test_ba_tenant_and_authz import _login

        c = app.test_client()
        with app.app_context():
            _login(c, user.id)
            resp = c.get("/api/batch-import/jobs")
            assert resp.status_code == 200, f"API failed: {resp.get_data(as_text=True)}"
            data = resp.get_json()
            assert data.get("success") is True
            jobs = data.get("jobs", [])
            assert len(jobs) == 1
            job_data = jobs[0]
            # Progress should be 0, not error
            assert job_data.get("progress") == 0.0
            assert job_data.get("progress_percentage") == 0.0
            assert job_data.get("batches_completed") == 0


@pytest.mark.usefixtures("db_session")
def test_job_with_null_counters_detail_page_renders(app, db_session, make_org, tenant_ctx):
    """Job detail page renders for a job with NULL counters."""
    from app.models.user import User
    from app.models.batch_import import BatchImportJob, BatchImportBatch, BatchJobStatus, BatchStatus
    from sqlalchemy import text

    org = make_org("null-counters-page")
    with tenant_ctx(org.id):
        user = User(
            email=f"nullcounters2-{org.id}@example.com",
            organization_id=org.id,
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        db_session.add(user)
        db_session.commit()

        # Create job through ORM
        job = BatchImportJob(
            job_uuid="null-counters-job-2",
            user_id=user.id,
            filename="test2.csv",
            total_applications=10,
            batch_size=20,
            total_batches=1,
            status=BatchJobStatus.PENDING,
        )
        db_session.add(job)
        db_session.flush()

        # Create batch
        batch = BatchImportBatch(
            job_id=job.id,
            batch_number=1,
            status=BatchStatus.QUEUED,
            total_applications=10,
        )
        db_session.add(batch)
        db_session.commit()
        job_id = job.id

        # Set counters to NULL via raw SQL
        db_session.execute(
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
        db_session.execute(
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
        db_session.commit()

        # Login and access the job detail page
        from tests.test_ba_tenant_and_authz import _login

        c = app.test_client()
        with app.app_context():
            _login(c, user.id)
            resp = c.get(f"/batch-import/jobs/{job_id}")
            assert resp.status_code == 200, f"Page failed: {resp.get_data(as_text=True)}"
            html = resp.get_data(as_text=True)
            # Page should render with content, not error
            assert "Import Job:" in html
            assert "test2.csv" in html
            # Progress should show 0%
            assert "0%" in html or "0.0" in html


@pytest.mark.usefixtures("db_session")
def test_batch_with_null_counters_progress_zero(app, db_session, make_org, tenant_ctx):
    """Batch progress_percentage returns 0 for NULL counters."""
    from app.models.user import User
    from app.models.batch_import import BatchImportJob, BatchImportBatch, BatchJobStatus, BatchStatus
    from sqlalchemy import text

    org = make_org("null-counters-batch")
    with tenant_ctx(org.id):
        user = User(
            email=f"nullcounters3-{org.id}@example.com",
            organization_id=org.id,
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        db_session.add(user)
        db_session.commit()

        job = BatchImportJob(
            job_uuid="null-counters-job-3",
            user_id=user.id,
            filename="test3.csv",
            total_applications=10,
            batch_size=20,
            total_batches=1,
            status=BatchJobStatus.PENDING,
        )
        db_session.add(job)
        db_session.flush()

        batch = BatchImportBatch(
            job_id=job.id,
            batch_number=1,
            status=BatchStatus.QUEUED,
            total_applications=10,
        )
        db_session.add(batch)
        db_session.commit()
        batch_id = batch.id

        # Set batch counters to NULL
        db_session.execute(
            text(
                """
            UPDATE batch_import_batch
            SET processed_applications = NULL,
                total_elements_generated = NULL,
                elements_approved = NULL,
                elements_rejected = NULL
            WHERE id = :id
            """
            ),
            {"id": batch_id},
        )
        db_session.commit()

        # Reload and test
        db_session.expire_all()
        batch = db_session.get(BatchImportBatch, batch_id)
        assert batch.progress_percentage == 0.0
        batch_dict = batch.to_dict()
        assert batch_dict["progress_percentage"] == 0.0
        # API returns 0 for NULL counters (normalized for frontend)
        assert batch_dict["processed_applications"] == 0
        assert batch_dict["total_elements_generated"] == 0
        assert batch_dict["elements_approved"] == 0
        assert batch_dict["elements_rejected"] == 0


@pytest.mark.usefixtures("db_session")
def test_get_job_progress_with_null_counters(app, db_session, make_org, tenant_ctx):
    """BatchImportService.get_job_progress handles NULL counters."""
    from app.models.user import User
    from app.models.batch_import import BatchImportJob, BatchImportBatch, BatchJobStatus, BatchStatus
    from app.modules.import_batch.services.batch_import_service import BatchImportService
    from sqlalchemy import text

    org = make_org("null-counters-service")
    with tenant_ctx(org.id):
        user = User(
            email=f"nullcounters4-{org.id}@example.com",
            organization_id=org.id,
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        db_session.add(user)
        db_session.commit()

        job = BatchImportJob(
            job_uuid="null-counters-job-4",
            user_id=user.id,
            filename="test4.csv",
            total_applications=10,
            batch_size=20,
            total_batches=1,
            status=BatchJobStatus.PENDING,
        )
        db_session.add(job)
        db_session.flush()

        batch = BatchImportBatch(
            job_id=job.id,
            batch_number=1,
            status=BatchStatus.QUEUED,
            total_applications=10,
        )
        db_session.add(batch)
        db_session.commit()
        job_id = job.id

        # Set all counters to NULL
        db_session.execute(
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
        db_session.execute(
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
        db_session.commit()

        service = BatchImportService()
        progress = service.get_job_progress(job_id)

        assert progress["job"]["progress"] == 0.0
        assert progress["totals"]["applications_processed"] == 0
        assert progress["totals"]["elements_generated"] == 0
        assert progress["totals"]["elements_approved"] == 0
        assert progress["totals"]["elements_rejected"] == 0
        assert progress["totals"]["elements_pending"] == 0