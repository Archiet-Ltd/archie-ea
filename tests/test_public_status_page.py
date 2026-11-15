"""The public status page answers 200 to anyone and keeps answering when a check fails."""

def test_signed_out_status_is_200_with_five_components(client):
    response = client.get("/status")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    for key in ("web", "database", "background-jobs", "ai-provider", "email"):
        assert 'data-testid="status-component-%s"' % key in body
    assert "<form" not in body.split("<main", 1)[1]


def test_database_check_failing_still_answers_200(client, monkeypatch):
    from app.modules.monitoring.services.health_service import HealthService

    monkeypatch.setattr(HealthService, "check_database", staticmethod(lambda: {"status": "unhealthy"}))
    response = client.get("/status")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Not responding" in body
    assert "Incident history is not available right now" in body


def test_a_check_that_cannot_run_reads_not_checked(client, monkeypatch):
    from app.modules.monitoring.services.health_service import HealthService

    def boom():
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(HealthService, "check_database", staticmethod(boom))
    response = client.get("/status")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "The check could not run" in body
    assert "probe exploded" not in body
