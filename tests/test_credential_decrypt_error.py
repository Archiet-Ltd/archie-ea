"""Test that credential decryption failures show a plain error message."""
import pytest
from unittest.mock import patch, MagicMock
from app.modules.codegen.services.credential_encryption import CredentialUnreadable
from app.services.connector_framework import build_connector, ConnectorWriteRefused
from app.models.connector_config import ConnectorConfig


def test_build_connector_handles_credential_unreadable(app):
    """When decrypt fails, build_connector raises ConnectorWriteRefused with a plain message."""
    with app.app_context():
        # Create a mock connector config
        cfg = ConnectorConfig(
            id=1,
            connector_type="github",
            name="Test GitHub",
            organization_id=1,
            config={"organization": "testorg"},
        )

        # Mock _credential_field_map to raise CredentialUnreadable
        with patch("app.services.connector_framework._credential_field_map") as mock_field_map:
            mock_field_map.side_effect = CredentialUnreadable("Failed to decrypt credential")

            # Attempting to build the connector should raise ConnectorWriteRefused with a plain message
            with pytest.raises(ConnectorWriteRefused) as exc_info:
                build_connector(cfg, org_id=1)

            # The message should be user-friendly, not "InvalidToken" or a technical error
            assert "We could not read the saved token" in str(exc_info.value)
            assert "Paste it again to reconnect" in str(exc_info.value)
            assert "InvalidToken" not in str(exc_info.value)
            assert "CredentialUnreadable" not in str(exc_info.value)
