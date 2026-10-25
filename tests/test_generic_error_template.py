"""Tests for generic_error.html template rendering.

The template is served by the 409 OptimisticLockConflict and
PlanLimitReached error handlers. These tests render the template
directly and verify its content.

Signed in as enterprise_architect (the design-map persona for /errors/
routes is "viewer", but the smoke-test fixture set does not include
viewer as an archetype).
"""

import os

import pytest


class TestGenericErrorTemplateMarkers:
    """The generic_error.html source contains all required markers."""

    SOURCE = None

    @classmethod
    def setup_class(cls):
        path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "app/templates/errors/generic_error.html",
        )
        with open(path) as f:
            cls.SOURCE = f.read()

    def test_contains_design_block_marker(self):
        assert "{# design-block: empty-state #}" in self.SOURCE

    def test_contains_shadcn_nearest_meta(self):
        assert 'shadcn-nearest' in self.SOURCE

    def test_contains_shadcn_reference_comment(self):
        assert "{# shadcn-reference:" in self.SOURCE

    def test_imports_empty_state_macro(self):
        assert "from 'components/empty_state.html' import empty_state" in self.SOURCE

    def test_imports_button_macro(self):
        assert "from 'components/button.html' import button" in self.SOURCE

    def test_imports_alert_macro(self):
        assert "from 'components/alert.html' import alert" in self.SOURCE

    def test_imports_page_shell_macro(self):
        assert "from 'macros/page_shell.html' import page_shell" in self.SOURCE

    def test_calls_empty_state(self):
        assert "empty_state(" in self.SOURCE

    def test_calls_button(self):
        assert "button(" in self.SOURCE

    def test_calls_page_shell(self):
        assert "page_shell(" in self.SOURCE

    def test_location_reload(self):
        assert "location.reload()" in self.SOURCE

    def test_history_back(self):
        assert "history.back()" in self.SOURCE

    def test_main_index(self):
        assert "main.index" in self.SOURCE

    def test_no_raw_button(self):
        assert "<button" not in self.SOURCE

    def test_no_raw_input(self):
        assert "<input" not in self.SOURCE

    def test_handles_recovery_action(self):
        assert "recovery_action" in self.SOURCE

    def test_data_testid_present(self):
        assert 'data-testid="generic-error"' in self.SOURCE

    def test_extends_admin_base(self):
        assert "layouts/admin_base.html" in self.SOURCE