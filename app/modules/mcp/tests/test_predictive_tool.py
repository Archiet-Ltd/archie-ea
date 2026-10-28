from __future__ import annotations

from unittest.mock import patch

import pytest
from flask import json

from app.models import User
from app.models.organization import Organization


@pytest.mark.usefixtures("app_context")
def test_get_predictive_insights(client, db_session):
    """Test get_predictive_insights MCP tool."""
    org = Organization(name="test-org")
    db_session.add(org)
    db_session.commit()

    user = User(email="test@example.com", password="test", organization_id=org.id, confirmed=True)
    db_session.add(user)
    db_session.commit()

    with patch("app.modules.ai_chat.tools.registry.run_all_checks") as mock_run_all_checks:
        mock_run_all_checks.return_value = [
            type("Prediction", (object,), {
                "prediction_type": "ZOMBIE_PROJECT",
                "severity": "HIGH",
                "target_element": "Business Case: Test Case",
                "evidence": ["Evidence 1", "Evidence 2"],
                "recommendation": "Recommendation 1",
            })
        ]

        response = client.post(
            "/mcp/tools/get_predictive_insights",
            headers={"Authorization": f"Bearer {user.get_auth_token()}"},
            json={"topic": "financial"},
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["result"][0]["prediction_type"] == "ZOMBIE_PROJECT"
