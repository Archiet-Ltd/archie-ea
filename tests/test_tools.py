"""Tests for the MCP tools."""

from unittest.mock import patch

import pytest

from app.modules.ai_chat.tools.route_read_tools import tool_released

ALL_TOOLS = [
    "ask_impact", "ask_strategy", "ask_portfolio", "ask_programme",
    "ask_risk", "ask_accountability", "get_element", "search_elements",
    "list_canvases", "get_canvas", "answer", "get_genome_slice", "ask_data_flow",
]


@pytest.mark.parametrize("release_wave, tool_name, expected_released", [
    (None, "ask_impact", True),
    (1, "ask_impact", False),
    (2, "ask_impact", True),
    (1, "get_element", True),
])
def test_tool_released(release_wave, tool_name, expected_released):
    """The tool_released function correctly reports tool status based on the release wave."""
    with patch("app.release_waves.configured_wave", return_value=release_wave):
        assert tool_released(tool_name) == expected_released
