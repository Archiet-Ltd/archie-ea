"""The onboarding modal must not interrogate a user who already has a role.

Found by driving a real browser: signing in as the SG Tadley business architect
put a "Welcome to Entelim — What's your role?" modal over the dashboard.
Three things were wrong with that, in ascending order of severity:

  1. It blocks the first screen of the product for a user who is not onboarding.
  2. Its list of eight roles did not include ``business_architect`` — the role
     that user actually holds. Production carries nine.
  3. Completing it POSTs ``enterprise_role`` to
     /dashboard/api/onboarding-complete, so answering the question **overwrites
     the real role**, silently re-scoping the sidebar and permissions. A user
     whose role is missing from the list cannot answer correctly, so the only
     available outcomes were "wrong role" or "dismiss".

The modal is now gated on the user having no ``enterprise_role`` at all, and
``business_architect`` is a first-class option for those who genuinely need it.
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


def test_the_users_current_role_is_preselected(app, make_user):
    """Completing the modal POSTs enterprise_role, so the default must be the truth.

    Preselection is what makes the write harmless for a user who already has a
    role: they confirm what they are rather than picking something else. It only
    works if the role is actually in the list — before this change a business
    architect was preselected to a value the picker could not render, so the
    first radio they touched would have changed their role.
    """
    user = make_user("business_architect")
    html = _render_base(app, user)

    assert "selectedRole: 'business_architect'" in html, (
        "the modal did not preselect the user's real enterprise_role, so "
        "completing it would overwrite that role"
    )


def test_modal_is_still_shown_to_a_genuinely_new_user(app, make_user):
    """The feature still works for whom it was built."""
    html = _render_base(app, make_user(None))

    assert MODAL_MARKER in html or MODAL_MARKER_RAW in html, (
        "a user with no enterprise_role should still be onboarded"
    )


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


def test_every_assignable_role_is_offered_by_the_picker(app, make_user):
    """A role the product assigns must be answerable in the picker.

    business_architect was assignable but absent from roleLabels, so a business
    architect's only options were a role that was not theirs, or dismissal —
    and picking one would have overwritten the real value.
    """
    html = _render_base(app, make_user(None))
    offered = set(re.findall(r"^\s*(\w+): '[^']+',\s*$", html, re.M))

    missing = [r for r in ASSIGNABLE_ROLES if r not in offered]
    assert not missing, (
        f"assignable but not offered by the onboarding role picker: {missing}"
    )



def test_onboarding_overlay_is_hidden_once_the_workspace_has_data(make_user, app):
    """ARCH-107: the overlay must NOT render over an instance that already
    holds data. It addressed an established user as a first-run visitor, and
    its @click.away dismissal consumed their first click on every page — the
    real cause of the "Add Application is inert" report (ARCH-040)."""
    user = make_user(role="business_architect")
    html = _render_base(app, user, nav_counts={"applications": 3})

    assert MODAL_MARKER not in html and MODAL_MARKER_RAW not in html, (
        "the first-run overlay rendered over a populated workspace, where it "
        "will silently swallow the user's first click"
    )


# ── T-UI-1: app shell overlay fixes ────────────────────────────────────────


def test_onboarding_panel_has_scroll_and_max_height_classes(app, make_user):
    """The onboarding panel must carry max-h and overflow-y-auto so it fits
    the viewport at 390x844 and scrolls when the role list overflows."""
    html = _render_base(app, make_user(None))

    # The panel (the card inside the fixed overlay) must be scrollable
    assert "max-h-[calc(100dvh-2rem)]" in html, (
        "onboarding panel missing max-h constraint; will be clipped at small viewports"
    )
    assert "overflow-y-auto" in html, (
        "onboarding panel missing overflow-y-auto; content below the fold is unreachable"
    )


def test_onboarding_step_3_persists_before_showing(app, make_user):
    """Entering step 3 must call persistOnboarding() before showing the
    'You're all set!' screen, so the modal does not reappear after navigation
    even if the user never clicks a button on step 3."""
    html = _render_base(app, make_user(None))

    # The step 2 Continue button must call persistOnboarding().then(...) before
    # setting step = 3, so the server persist happens on entry to step 3.
    # Match the @click attribute specifically, not the method definition.
    assert re.search(r'@click="persistOnboarding\(\)\.then\(', html), (
        "step 2 Continue button must call persistOnboarding().then(...) in its @click attribute"
    )
    # Step 3's CTA must use dismissOnboarding (not completeOnboarding) since
    # persistence already happened on entry.
    assert 'dismissOnboarding()' in html, (
        "step 3 buttons must use dismissOnboarding(), not completeOnboarding()"
    )
    assert "You're all set!" in html or "You&#39;re all set!" in html, (
        "step 3 copy must be present"
    )


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
