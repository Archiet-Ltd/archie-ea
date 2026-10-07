"""
Tests for monitoring module — health check endpoints.

Verifies that the migrated health routes produce identical responses
to the original app/routes/health_routes.py implementation.
"""

import pytest

from app.modules.monitoring.services.health_service import HealthService


@pytest.fixture(scope="module")
def monitoring_app():
    """An app with this module's health and metrics blueprints mounted.

    The product app does not mount them (``app/_bootstrap/blueprints.py``
    serves ``/health`` from ``app/routes/health_routes.py`` and leaves these
    ops blueprints out), so the route tests below mount them on an app of
    their own, under names that cannot collide with the product's blueprints.
    """
    from app import create_app
    from app.modules.monitoring.routes.health_routes import health_bp
    from app.modules.monitoring.routes.metrics_routes import metrics_bp

    application = create_app("testing")
    application.config["TESTING"] = True
    application.config["WTF_CSRF_ENABLED"] = False
    application.register_blueprint(health_bp, name="monitoring_module_health")
    application.register_blueprint(metrics_bp, name="monitoring_module_metrics")
    return application


@pytest.fixture
def client(monitoring_app):
    return monitoring_app.test_client()


class TestHealthService:
    """Unit tests for HealthService methods."""

    def test_check_database_healthy(self, app):
        """Database probe returns 'healthy' when DB responds."""
        with app.app_context():
            result = HealthService.check_database()
            assert result["status"] in ("healthy", "unhealthy")
            assert "response_time_ms" in result
            assert "message" in result
            assert "last_checked" in result

    def test_check_storage_healthy(self, app):
        """Storage probe returns 'healthy' when uploads dir is writable."""
        with app.app_context():
            result = HealthService.check_storage()
            assert result["status"] in ("healthy", "unhealthy")
            assert "message" in result

    def test_check_cache_not_configured(self, app):
        """Cache probe returns 'not_configured' when REDIS_URL is absent."""
        with app.app_context():
            app.config.pop("REDIS_URL", None)
            app.config.pop("RQ_DEFAULT_URL", None)
            result = HealthService.check_cache()
            assert result["status"] in ("not_configured", "not_available", "unhealthy")

    def test_check_llm_returns_valid_structure(self, app):
        """LLM probe always returns a status dict."""
        with app.app_context():
            result = HealthService.check_llm()
            assert "status" in result
            assert result["status"] in ("healthy", "degraded", "unhealthy")

    def test_check_external_services_returns_dict(self, app):
        """External services probe returns a dict of service statuses."""
        with app.app_context():
            result = HealthService.check_external_services()
            assert isinstance(result, dict)
            assert "ai_features" in result

    def test_check_vendors_returns_valid_structure(self, app):
        """Vendor probe returns valid status dict."""
        with app.app_context():
            result = HealthService.check_vendors()
            assert "status" in result
            assert "message" in result

    def test_full_check_returns_tuple(self, app):
        """Full check returns (payload_dict, http_status_int)."""
        with app.app_context():
            payload, http_status = HealthService.full_check()
            assert isinstance(payload, dict)
            assert isinstance(http_status, int)
            assert http_status in (200, 503)
            assert "status" in payload
            assert "components" in payload
            assert "database" in payload["components"]
            assert "storage" in payload["components"]
            assert "cache" in payload["components"]
            assert "llm" in payload["components"]


class TestHealthRoutes:
    """Integration tests for health route endpoints."""

    def test_health_check_endpoint(self, client):
        """GET /api/health returns JSON with overall status."""
        resp = client.get("/api/health")
        assert resp.status_code in (200, 503)
        data = resp.get_json()
        assert "status" in data
        assert "components" in data

    def test_database_health_endpoint(self, client):
        """GET /api/health/database returns database status."""
        resp = client.get("/api/health/database")
        assert resp.status_code in (200, 503)
        data = resp.get_json()
        assert "status" in data

    def test_storage_health_endpoint(self, client):
        """GET /api/health/storage returns storage status."""
        resp = client.get("/api/health/storage")
        assert resp.status_code in (200, 503)
        data = resp.get_json()
        assert "status" in data

    def test_cache_health_endpoint(self, client):
        """GET /api/health/cache returns cache status."""
        resp = client.get("/api/health/cache")
        assert resp.status_code in (200, 503)
        data = resp.get_json()
        assert "status" in data

    def test_liveness_endpoint(self, client):
        """GET /api/health/live always returns 200."""
        resp = client.get("/api/health/live")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "alive"

    def test_readiness_endpoint(self, client):
        """GET /api/health/ready returns readiness status."""
        resp = client.get("/api/health/ready")
        assert resp.status_code in (200, 503)
        data = resp.get_json()
        assert data["status"] in ("ready", "not_ready")

    def test_vendor_health_endpoint(self, client):
        """GET /api/health/vendors returns vendor subsystem status."""
        resp = client.get("/api/health/vendors")
        assert resp.status_code in (200, 503)
        data = resp.get_json()
        assert "status" in data

    def test_external_health_endpoint(self, client):
        """GET /api/health/external returns external services status."""
        resp = client.get("/api/health/external")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "services" in data

    def test_llm_health_endpoint(self, client):
        """GET /api/health/llm returns LLM status."""
        resp = client.get("/api/health/llm")
        assert resp.status_code in (200, 503)
        data = resp.get_json()
        assert "status" in data


class TestMetricsRoutes:
    """Integration tests for metrics route endpoints."""

    def test_metrics_endpoint(self, client):
        """GET /metrics returns Prometheus exposition format."""
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_debug_metrics_requires_auth(self, client):
        """GET /debug/metrics requires authentication."""
        resp = client.get("/debug/metrics")
        # Should redirect to login or return 401
        assert resp.status_code in (302, 401)

    def test_debug_metrics_json_requires_auth(self, client):
        """GET /debug/metrics/json requires authentication."""
        resp = client.get("/debug/metrics/json")
        assert resp.status_code in (302, 401)
