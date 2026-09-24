"""Export complete solution for customer infrastructure deployment.

Assembles generated code, deployment configs, migration scripts, n8n workflows,
Keycloak realm, Grafana dashboards, test suite, and documentation into a
platform-specific package. Credentials are replaced with placeholders.

Target platforms: docker-compose, kubernetes, aws, azure, gcp
"""
import json
import logging
import re
from datetime import datetime

from app.modules.codegen.models import CodegenGeneration, SolutionVersion

logger = logging.getLogger(__name__)

_SECRET_PATTERN = re.compile(
    r"""(?:SECRET_KEY|API_KEY|PASSWORD|TOKEN|CREDENTIAL)\s*=\s*['"]?([^'"\s]+)['"]?""",
    re.IGNORECASE,
)

_VALID_PLATFORMS = frozenset({"docker-compose", "kubernetes", "aws", "azure", "gcp"})


class MigrationPackager:
    """Export solution packages for customer infrastructure."""

    def export(self, solution_id: int, target_platform: str = "docker-compose") -> dict:
        """Export a complete solution package.

        Args:
            solution_id: Solution to export
            target_platform: docker-compose, kubernetes, aws, azure, gcp

        Returns:
            dict with 'files' (dict of path->content), 'metadata'.

        Raises:
            ValueError: If solution has no generated code or platform is unknown.
        """
        if target_platform not in _VALID_PLATFORMS:
            raise ValueError(
                f"Unknown platform: {target_platform}. "
                f"Valid: {sorted(_VALID_PLATFORMS)}"
            )

        gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
        if not gen or not gen.generated_files:
            raise ValueError(f"No generated code for solution {solution_id}")

        files = dict(gen.generated_files)

        # Sanitize credentials
        files = self._sanitize_credentials(files)

        # Platform-specific deployment configs
        from app.modules.codegen.services.platform_configs import generate_platform_configs
        platform_files = generate_platform_configs(target_platform, {
            "solution_id": solution_id,
            "solution_name": f"solution-{solution_id}",
            "language": getattr(gen, "language", "python-fastapi") or "python-fastapi",
            "database": "postgresql",
            "port": 8000,
            "env_vars": self._extract_env_vars(files),
        })
        files.update(platform_files)

        # Standard files
        files[".env.example"] = self._generate_env_example(files)
        files["README.md"] = self._generate_readme(solution_id, target_platform, files)

        # n8n workflow exports
        n8n_files = self._export_n8n_workflows(solution_id)
        files.update(n8n_files)

        # Test suite
        test_files = self._generate_test_suite(solution_id)
        files.update(test_files)

        # Grafana dashboards
        grafana_files = self._export_grafana_dashboards(solution_id)
        files.update(grafana_files)

        # Migration scripts
        versions = (
            SolutionVersion.query.filter_by(solution_id=solution_id)
            .order_by(SolutionVersion.version_number.desc())
            .all()
        )
        if versions:
            migration_sql = []
            for v in reversed(versions):
                if v.migration_scripts and v.migration_scripts.get("forward"):
                    migration_sql.append(
                        f"-- Version {v.version_number}: {v.change_summary}\n"
                        f"{v.migration_scripts['forward']}"
                    )
            if migration_sql:
                files["migrations/combined.sql"] = "\n\n".join(migration_sql)

        return {
            "files": files,
            "metadata": {
                "solution_id": solution_id,
                "target_platform": target_platform,
                "file_count": len(files),
                "exported_at": datetime.utcnow().isoformat(),
            },
        }

    def _sanitize_credentials(self, files: dict) -> dict:
        sanitized = {}
        for path, content in files.items():
            sanitized[path] = _SECRET_PATTERN.sub(
                lambda m: m.group(0).split("=")[0] + "=REPLACE_ME",
                content if isinstance(content, str) else str(content),
            )
        return sanitized

    def _extract_env_vars(self, files: dict) -> list:
        """Extract environment variable names referenced in code."""
        env_vars = set()
        for content in files.values():
            if isinstance(content, str):
                for match in re.finditer(r'os\.environ\.get\(["\'](\w+)["\']', content):
                    env_vars.add(match.group(1))
                for match in re.finditer(r'os\.getenv\(["\'](\w+)["\']', content):
                    env_vars.add(match.group(1))
        return sorted(env_vars)

    def _generate_env_example(self, files: dict) -> str:
        env_vars = set()
        for content in files.values():
            if isinstance(content, str):
                for match in re.finditer(r'os\.environ\.get\(["\'](\w+)["\']', content):
                    env_vars.add(match.group(1))
                for match in re.finditer(r'os\.getenv\(["\'](\w+)["\']', content):
                    env_vars.add(match.group(1))

        lines = [
            "# Environment variables for this application",
            "# Copy to .env and fill in values",
            "",
        ]
        for var in sorted(env_vars):
            lines.append(f"{var}=")

        lines.extend([
            "",
            "DATABASE_URL=postgresql://user:password@localhost:5432/mydb",
            "SECRET_KEY=change-me-in-production",
            "",
        ])
        return "\n".join(lines)

    def _generate_readme(self, solution_id: int, platform: str, files: dict) -> str:
        file_count = len(files)
        deploy_md = files.get("DEPLOY.md", "")
        deploy_section = f"\nSee `DEPLOY.md` for {platform}-specific deployment instructions.\n" if deploy_md else ""

        return f"""# Solution {solution_id} -- Deployment Package

## Overview

This is a self-contained deployment package exported from the Entelim Platform.
It includes all application code, deployment configs, migration scripts, and
documentation needed to run this solution on your own infrastructure.

## Target Platform: {platform}
{deploy_section}
## Quick Start

```bash
cp .env.example .env
# Edit .env with your values -- all credentials are placeholders
```

## Files

This package contains {file_count} files:
- Application code
- Deployment configs ({platform})
- Database migration scripts (if any)
- n8n workflow definitions (if any)
- Test suite
- Documentation

## Environment Variables

See `.env.example` for required configuration.

## Migrations

If `migrations/combined.sql` is present, run it against your database after first deployment.

## Testing

```bash
pytest tests/ -v
```

---
*Exported from Entelim Platform*
"""

    def _export_n8n_workflows(self, solution_id: int) -> dict:
        """Export compiled n8n workflows for this solution.

        Errors propagate. `export()` is the last step before the customer
        deploys this package on their own infrastructure, and an empty dict
        here silently produced a package with none of their automation
        workflows in it — the README still promises "n8n workflow definitions
        (if any)", so nothing on the receiving end reveals the omission. The
        route caller already logs and returns 500, which is recoverable;
        shipping a quietly incomplete package is not.
        """
        from app.modules.codegen.models import WorkflowDesign

        workflows = WorkflowDesign.query.filter_by(
            solution_id=solution_id, is_active=True
        ).all()

        files = {}
        for wf in workflows:
            if wf.compiled_n8n:
                safe_name = re.sub(r'[^a-z0-9_-]', '-', wf.name.lower().strip())
                files[f"n8n/workflow-{safe_name}.json"] = json.dumps(
                    wf.compiled_n8n, indent=2
                )
        return files

    def _export_grafana_dashboards(self, solution_id: int) -> dict:
        """Generate a basic Grafana dashboard for the solution."""
        dashboard = {
            "dashboard": {
                "title": f"Solution {solution_id} -- Monitoring",
                "panels": [
                    {
                        "title": "Request Rate",
                        "type": "graph",
                        "targets": [{"expr": "rate(http_requests_total[5m])"}],
                    },
                    {
                        "title": "Error Rate",
                        "type": "graph",
                        "targets": [{"expr": 'rate(http_requests_total{status=~"5.."}[5m])'}],
                    },
                    {
                        "title": "Response Time (p95)",
                        "type": "graph",
                        "targets": [{"expr": "histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))"}],
                    },
                ],
            },
        }
        return {
            "grafana/dashboard.json": json.dumps(dashboard, indent=2),
        }

    def _generate_test_suite(self, solution_id: int) -> dict:
        """Generate a basic test suite for the exported solution."""
        files = {}

        files["tests/conftest.py"] = '''"""Test configuration."""
import pytest


@pytest.fixture
def client():
    """Create test client."""
    from app.main import app
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client
'''

        files["tests/test_health.py"] = '''"""Health check smoke tests."""


def test_health_endpoint(client):
    """Health endpoint returns 200."""
    resp = client.get("/health")
    assert resp.status_code == 200


def test_health_returns_json(client):
    """Health endpoint returns JSON with status field."""
    resp = client.get("/health")
    data = resp.get_json()
    assert "status" in data
'''

        return files
