"""No role wizard or feature tour is drawn over the first screen.

A new owner used to meet a "Welcome ... What's your role?" modal with eleven roles and a second
"Key features for you" screen before anything else. The persona now defaults at sign-up
(``User.DEFAULT_PERSONA_LENS``) and is changed later, so the shell renders no such modal for anyone,
whatever role they hold and whether or not the workspace is empty.
"""

import re

import pytest

MODAL_MARKER = "What&#39;s your role?"
MODAL_MARKER_RAW = "What's your role?"


def _render_base(app, user, nav_counts=None):
    """Render a page extending layouts/admin_base.html as *user*.

    nav_counts is passed EXPLICITLY and defaults to empty. ARCH-107 gates the
    onboarding overlay on an empty workspace — the overlay used to render over
    instances that already held data, and because its card dismisses on
    @click.away it silently ate the user's first click anywhere on the page,
    which is what made "Add Application" look inert (ARCH-040).

    These tests are about what the modal CONTAINS, so they must control that
    gate rather than inherit whatever the shared test database happens to hold;
    otherwise they pass or fail depending on which tests ran first. The gate
    itself is pinned separately below.
    """
    from flask import render_template_string
    from flask_login import login_user

    with app.test_request_context("/"):
        login_user(user)
        return render_template_string(
            "{% extends 'layouts/admin_base.html' %}{% block content %}x{% endblock %}",
            nav_counts=nav_counts if nav_counts is not None else {},
        )


@pytest.fixture
def make_user(db_session, make_org):
    from app.models.user import User

    def _make(role):
        org = make_org(f"onboard-{role or 'none'}")
        user = User(
            email=f"onboard-{role or 'none'}-{org.id}@example.com",
            first_name="On",
            last_name="Board",
            confirmed=True,
        )
        user.organization_id = org.id
        user.enterprise_role = role
        user.onboarding_completed_at = None
        db_session.add(user)
        db_session.commit()
        return user

    return _make






# The enterprise roles the product actually assigns — one seeded user per role
# exists in production for exactly this set.
ASSIGNABLE_ROLES = (
    "solution_architect",
    "enterprise_architect",
    "business_architect",
    "arb_member",
    "portfolio_manager",
    "cto",
    "application_manager",
    "procurement",
    "platform_admin",
)







# ── T-UI-1: app shell overlay fixes ────────────────────────────────────────






@pytest.mark.parametrize("role", [None, "business_architect", "cto", "platform_admin"])
@pytest.mark.parametrize("counts", [{}, {"applications": 3}])
def test_no_role_wizard_is_rendered(app, make_user, role, counts):
    """Whoever signs in, to an empty or a populated workspace, no role question is drawn."""
    html = _render_base(app, make_user(role), nav_counts=counts)

    for phrase in (MODAL_MARKER, MODAL_MARKER_RAW, "Key features for you", "showOnboarding", "roleLabels"):
        assert phrase not in html, phrase


def test_phone_opener_z_index_below_backdrop(app, make_user):
    """The phone sidebar opener (z-30) must sit below the mobile sidebar
    backdrop (z-40) so it never draws over content or modal backdrops."""
    html = _render_base(app, make_user(None))

    # The opener button must have z-30, not z-[60]
    assert 'z-30' in html, (
        "phone opener z-index must be 30, below the backdrop's 40"
    )
    # The backdrop must still be z-40
    assert 'z-40' in html, (
        "mobile sidebar backdrop must remain at z-40"
    )
    # The opener must NOT carry z-[60] any more
    assert 'z-[60]' not in html, (
        "phone opener must not use z-[60]; it draws over content and modals"
    )


def test_help_button_z_index_below_overlays(app, make_user):
    """The help button must sit at z-30, below every overlay (backdrop z-40,
    drawer z-50, modal z-[100])."""
    html = _render_base(app, make_user(None))

    # The guided-mode trigger must be present
    assert 'guided-mode-trigger' in html, (
        "guided mode trigger must be present in the template"
    )
    # The trigger button must carry z-30, not z-40
    trigger_match = re.search(r'id="guided-mode-trigger".*?class="([^"]*)"', html, re.DOTALL)
    assert trigger_match, "guided-mode-trigger element not found with class attribute"
    trigger_classes = trigger_match.group(1)
    assert 'z-30' in trigger_classes, (
        f"guided-mode-trigger must use z-30, got classes: {trigger_classes}"
    )
    assert 'z-40' not in trigger_classes, (
        f"guided-mode-trigger must not use z-40, got classes: {trigger_classes}"
    )
    # The panel must also carry z-30, not z-40
    panel_match = re.search(r'id="guided-mode-panel".*?class="([^"]*)"', html, re.DOTALL)
    assert panel_match, "guided-mode-panel element not found with class attribute"
    panel_classes = panel_match.group(1)
    assert 'z-30' in panel_classes, (
        f"guided-mode-panel must use z-30, got classes: {panel_classes}"
    )
    assert 'z-40' not in panel_classes, (
        f"guided-mode-panel must not use z-40, got classes: {panel_classes}"
    )
