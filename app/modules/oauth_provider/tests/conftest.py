"""Collection rules for the connector tests; the shared fixtures come from app/modules/conftest.py.

Which of these tests run in a session is decided by scripts/ci/mcp_selection.py (loaded through
pytest.ini): with MCP_ENABLED off they are deselected, with it on only they run.
"""
