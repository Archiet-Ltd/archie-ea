"""Tests for the Business Architect persona (role, nav access, AI charter).

Business Architect is a new first-class `enterprise_role`, added alongside the
existing 8 North Star personas. These tests are written to run WITHOUT a live
database wherever possible:

- Role constants / display names: pure Python, no DB.
- `role_access.get_visible_sections`: takes a duck-typed stub with an
  `enterprise_role` attribute — no ORM `User` instance required.
- `architect_persona_charters.build_architect_prompt`: every live-data section
  is wrapped in a fault-tolerant try/except (`_safe()`), so it degrades to
  "unavailable" lines instead of raising when there is no DB/app context —
  the charter text itself still comes back.
"""

import pytest


class TestBusinessArchitectRole:
    """app/models/user.py — role constant, VALID_ROLES, display name."""

    def test_role_constant_defined(self):
        from app.models.user import ROLE_BUSINESS_ARCHITECT

        assert ROLE_BUSINESS_ARCHITECT == "business_architect"

    def test_role_in_valid_roles(self):
        from app.models.user import ROLE_BUSINESS_ARCHITECT, VALID_ROLES

        assert ROLE_BUSINESS_ARCHITECT in VALID_ROLES

    def test_role_has_display_name(self):
        from app.models.user import ROLE_BUSINESS_ARCHITECT, ROLE_DISPLAY_NAMES

        assert ROLE_DISPLAY_NAMES.get(ROLE_BUSINESS_ARCHITECT) == "Business Architect"


class TestBusinessArchitectSectionAccess:
    """app/utils/role_access.py — sidebar section visibility for the new role.

    Uses a plain stub object (duck-typing `enterprise_role`) instead of a real
    `User` model instance, so no database is required.
    """

    class _StubUser:
        def __init__(self, enterprise_role):
            self.enterprise_role = enterprise_role

    def test_business_architecture_section_registered(self):
        from app.utils.role_access import NAVIGATION_SECTIONS

        assert "business_architecture" in NAVIGATION_SECTIONS

    def test_visible_sections_include_business_architecture_and_capabilities(self):
        from app.utils.role_access import get_visible_sections

        user = self._StubUser("business_architect")
        visible = get_visible_sections(user)

        assert "business_architecture" in visible
        assert "capabilities" in visible
        assert "architecture" in visible
        assert "home" in visible

    def test_can_access_section_true_for_business_architecture(self):
        from app.utils.role_access import can_access_section

        user = self._StubUser("business_architect")
        assert can_access_section(user, "business_architecture") is True

    def test_enterprise_architect_and_platform_admin_also_get_new_section(self):
        from app.utils.role_access import get_visible_sections

        ea_visible = get_visible_sections(self._StubUser("enterprise_architect"))

        class _PlatformAdminStub(self._StubUser):
            # Authority comes from the account's flag and role, not the persona.
            is_platform_admin = True

            def can(self, permission):
                return True

        admin_visible = get_visible_sections(_PlatformAdminStub("platform_admin"))

        assert "business_architecture" in ea_visible
        assert "business_architecture" in admin_visible

    def test_unrelated_role_does_not_get_business_architecture(self):
        from app.utils.role_access import get_visible_sections

        procurement_visible = get_visible_sections(self._StubUser("procurement"))
        assert "business_architecture" not in procurement_visible


class TestBusinessArchitectCharter:
    """app/modules/ai_chat/services/architect_persona_charters.py"""

    def test_persona_registered_as_governed_architect(self):
        from app.modules.ai_chat.services.architect_persona_charters import (
            ARCHITECT_PERSONAS,
        )

        assert "business_architect" in ARCHITECT_PERSONAS

    def test_charter_text_defined(self):
        from app.modules.ai_chat.services.architect_persona_charters import CHARTERS

        charter = CHARTERS.get("business_architect")
        assert charter is not None
        assert "Business Architect" in charter
        assert "capabilit" in charter.lower()

    def test_context_builder_registered(self):
        from app.modules.ai_chat.services.architect_persona_charters import (
            _CONTEXT_BUILDERS,
        )

        assert "business_architect" in _CONTEXT_BUILDERS
        assert callable(_CONTEXT_BUILDERS["business_architect"])

    def test_build_architect_prompt_returns_non_none_string_with_charter(self):
        from app.modules.ai_chat.services.architect_persona_charters import (
            build_architect_prompt,
        )

        prompt = build_architect_prompt("business_architect")

        assert prompt is not None
        assert isinstance(prompt, str)
        assert "Business Architect" in prompt
        assert "Live Platform Data" in prompt

    def test_build_architect_prompt_none_for_unknown_persona(self):
        from app.modules.ai_chat.services.architect_persona_charters import (
            build_architect_prompt,
        )

        assert build_architect_prompt("not_a_real_persona") is None


@pytest.mark.smoke
class TestBusinessArchitectImports:
    """Import-smoke: modules touched by the Business Architect persona work
    still import cleanly together (catches circular-import / typo regressions)."""

    def test_all_touched_modules_import(self):
        import app.models.user  # noqa: F401
        import app.utils.role_access  # noqa: F401
        import app.modules.ai_chat.services.architect_persona_charters  # noqa: F401
        import app.modules.ai_chat.services.multi_domain_chat_service  # noqa: F401

    def test_multi_domain_chat_service_has_business_architect_persona_config(self):
        from app.modules.ai_chat.services.multi_domain_chat_service import (
            PERSONA_CONFIGS,
        )

        assert "business_architect" in PERSONA_CONFIGS
