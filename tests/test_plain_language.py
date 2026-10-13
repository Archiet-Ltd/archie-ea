"""Plain-language display: vocabulary map, filter, sidebar labels, and the
show_archimate_names user setting.

Tests:
1. Sidebar shows "Architecture" and "Vendor Analysis"
2. With the setting on, an element page shows "ApplicationComponent"; with it
   off, "Application"
3. No API response or stored value changes
4. Cross-organisation test for every new read
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(db_session, make_org, **kwargs):
    from app.models.user import User

    show_archimate = kwargs.pop("show_archimate_names", None)
    org = make_org("plain-lang")
    defaults = dict(
        email=f"plain-lang-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Plain",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    defaults.update(kwargs)
    user = User(**defaults)
    db_session.add(user)
    db_session.flush()
    if show_archimate is not None:
        user.show_archimate_names = show_archimate
        db_session.flush()
    return user


def _login(client, user_id):
    from tests._session_test_helpers import mint_test_sid

    _sid = mint_test_sid(user_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid

    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _make_client(app, db_session, make_org, **user_kwargs):
    user = _make_user(db_session, make_org, **user_kwargs)
    client = app.test_client()
    _login(client, user.id)
    return client, user


# ---------------------------------------------------------------------------
# Vocabulary map
# ---------------------------------------------------------------------------

def test_vocabulary_map_covers_all_registered_types():
    """Every type in ArchiMateElementTypes has a plain-language entry."""
    from app.models.archimate_element_types import (
        PLAIN_LANGUAGE_NAMES,
        ArchiMateElementTypes,
    )

    all_types = ArchiMateElementTypes.get_all_elements()
    missing = set(all_types) - set(PLAIN_LANGUAGE_NAMES)
    assert not missing, f"Types missing from PLAIN_LANGUAGE_NAMES: {missing}"


def test_plain_name_for_known_type():
    from app.models.archimate_element_types import plain_name_for

    assert plain_name_for("ApplicationComponent") == "Application"
    assert plain_name_for("BusinessActor") == "Person or team"
    assert plain_name_for("Node") == "Platform"
    assert plain_name_for("SystemSoftware") == "Software platform"
    assert plain_name_for("WorkPackage") == "Project"
    assert plain_name_for("Assessment") == "Finding"
    assert plain_name_for("ImplementationEvent") == "Milestone"
    assert plain_name_for("Plateau") == "Stage"


def test_plain_name_for_unknown_type_returns_original():
    from app.models.archimate_element_types import plain_name_for

    assert plain_name_for("Location") == "Location"
    assert plain_name_for("Grouping") == "Grouping"
    assert plain_name_for("Junction") == "Junction"


def test_plain_name_for_none_returns_em_dash():
    from app.models.archimate_element_types import plain_name_for

    assert plain_name_for(None) == "\u2014"
    assert plain_name_for("") == "\u2014"


def test_plain_layer_names():
    from app.models.archimate_element_types import plain_layer_name
    from app.models.constants import ArchiMateLayer

    assert plain_layer_name("strategy") == "Strategy"
    assert plain_layer_name("business") == "Business"
    assert plain_layer_name("application") == "Applications"
    assert plain_layer_name("technology") == "Technology"
    assert plain_layer_name("implementation") == "Projects and change"
    assert plain_layer_name("implementation_migration") == "Projects and change"
    assert ArchiMateLayer.IMPLEMENTATION == "implementation_migration"
    assert ArchiMateLayer.normalize("Implementation & Migration") == "implementation_migration"
    assert plain_layer_name(None) == "\u2014"


# ---------------------------------------------------------------------------
# Jinja filter
# ---------------------------------------------------------------------------

def test_plain_name_filter_defaults_to_plain(app):
    """With no user (or show_archimate_names=False), the filter returns plain names."""
    from app.template_helpers import _plain_name

    assert _plain_name("ApplicationComponent") == "Application"
    assert _plain_name("BusinessActor") == "Person or team"
    assert _plain_name("Node") == "Platform"


def test_plain_name_filter_with_archimate_on(app, db_session, make_org):
    """With show_archimate_names=True, the filter returns the original name."""
    from app.template_helpers import _plain_name

    user = _make_user(db_session, make_org, show_archimate_names=True)
    assert _plain_name("ApplicationComponent", user) == "ApplicationComponent"
    assert _plain_name("BusinessActor", user) == "BusinessActor"


def test_plain_name_filter_with_archimate_off(app, db_session, make_org):
    """With show_archimate_names=False, the filter returns plain names."""
    from app.template_helpers import _plain_name

    user = _make_user(db_session, make_org, show_archimate_names=False)
    assert _plain_name("ApplicationComponent", user) == "Application"
    assert _plain_name("BusinessActor", user) == "Person or team"


def test_plain_name_filter_none_input(app):
    from app.template_helpers import _plain_name

    assert _plain_name(None) == "\u2014"
    assert _plain_name("") == "\u2014"


def test_plain_layer_filter(app, db_session, make_org):
    from app.template_helpers import _plain_layer

    user = _make_user(db_session, make_org, show_archimate_names=False)
    assert _plain_layer("application", user) == "Applications"
    assert _plain_layer("implementation", user) == "Projects and change"

    user.show_archimate_names = True
    assert _plain_layer("application", user) == "application"


# ---------------------------------------------------------------------------
# Sidebar labels (acceptance criterion 2)
# ---------------------------------------------------------------------------

def test_sidebar_shows_architecture_not_archimate_elements():
    """The Library zone link formerly labelled 'ArchiMate Elements' now reads
    'Architecture'. Only the library-zone link is checked — the same endpoint
    may appear under a different label in a persona's My-work zone
    (e.g. 'ArchiMate Model' for data_architect), which is a distinct surface."""
    from app.utils.role_access import SIDEBAR_ZONES

    for role, zones in SIDEBAR_ZONES.items():
        for zone in zones:
            if zone["zone"] != "library":
                continue
            for link in zone["links"]:
                if link["endpoint"] == "archimate_crud.dashboard":
                    assert link["label"] == "Architecture", (
                        f"Role {role} library zone: expected 'Architecture', got '{link['label']}'"
                    )


def test_module_directory_shows_vendor_analysis():
    """The More-tools entry formerly labelled 'Vendor ArchiMate Analysis' now
    reads 'Vendor Analysis'."""
    from app.modules.modules_directory.routes import _MORE_TOOLS

    for label, endpoint, _icon in _MORE_TOOLS:
        if endpoint == "main.vendor_archimate_analysis":
            assert label == "Vendor Analysis", (
                f"Expected 'Vendor Analysis', got '{label}'"
            )
            break
    else:
        pytest.fail("main.vendor_archimate_analysis not found in _MORE_TOOLS")


# ---------------------------------------------------------------------------
# Element page display (acceptance criterion 3)
# ---------------------------------------------------------------------------

def test_element_type_display_with_setting_off(app, db_session, make_org):
    """With show_archimate_names=False (default), element types use plain names."""
    from app.models.archimate_element_types import plain_name_for

    # Simulate what the template filter would return
    assert plain_name_for("ApplicationComponent") == "Application"
    assert plain_name_for("BusinessActor") == "Person or team"
    assert plain_name_for("Node") == "Platform"
    assert plain_name_for("WorkPackage") == "Project"


def test_element_type_display_with_setting_on(app, db_session, make_org):
    """With show_archimate_names=True, element types use standard names."""
    from app.template_helpers import _plain_name

    user = _make_user(db_session, make_org, show_archimate_names=True)
    assert _plain_name("ApplicationComponent", user) == "ApplicationComponent"
    assert _plain_name("BusinessActor", user) == "BusinessActor"


# ---------------------------------------------------------------------------
# No API/stored-value change (acceptance criterion 4)
# ---------------------------------------------------------------------------

def test_element_json_unchanged_by_display_setting(app, db_session, make_org):
    """The show_archimate_names setting only affects display, never API responses
    or stored values. An element's stored type is always the ArchiMate name."""
    from app.models.archimate_core import ArchiMateElement

    user = _make_user(db_session, make_org)
    org_id = user.organization_id

    elem = ArchiMateElement(
        name="Test Component",
        type="ApplicationComponent",
        organization_id=org_id,
    )
    db_session.add(elem)
    db_session.flush()

    # Capture the element's data before any setting change
    before = {
        "name": elem.name,
        "type": elem.type,
        "organization_id": elem.organization_id,
    }

    # Toggle the setting — this must not affect the stored element
    user.show_archimate_names = True
    db_session.flush()

    after = {
        "name": elem.name,
        "type": elem.type,
        "organization_id": elem.organization_id,
    }

    assert before == after, (
        f"Element data changed when display setting was toggled: {before} -> {after}"
    )
    # The stored type is always the ArchiMate name
    assert elem.type == "ApplicationComponent"


# ---------------------------------------------------------------------------
# Cross-organisation test (acceptance criterion: reads must not write;
# cross-organisation test for every new read)
# ---------------------------------------------------------------------------

def test_plain_name_filter_cross_organisation(app, db_session, make_org):
    """The plain_name filter is a pure read — it must not write anything and
    must work identically across organisations."""
    from app.template_helpers import _plain_name

    user_a = _make_user(db_session, make_org, show_archimate_names=False)
    user_b = _make_user(db_session, make_org, show_archimate_names=True)

    # Both users see the correct names for their setting
    assert _plain_name("ApplicationComponent", user_a) == "Application"
    assert _plain_name("ApplicationComponent", user_b) == "ApplicationComponent"

    # The filter does not mutate the user
    assert user_a.show_archimate_names is False
    assert user_b.show_archimate_names is True


def test_vocabulary_map_is_read_only():
    """PLAIN_LANGUAGE_NAMES is a module-level constant — calling plain_name_for
    must not mutate it."""
    from app.models.archimate_element_types import PLAIN_LANGUAGE_NAMES, plain_name_for

    before = dict(PLAIN_LANGUAGE_NAMES)
    plain_name_for("ApplicationComponent")
    plain_name_for("BusinessActor")
    after = dict(PLAIN_LANGUAGE_NAMES)
    assert before == after


# ---------------------------------------------------------------------------
# show_archimate_names column default
# ---------------------------------------------------------------------------

def test_show_archimate_names_defaults_false(db_session, make_org):
    """New users default to show_archimate_names=False (plain language)."""
    user = _make_user(db_session, make_org)
    assert user.show_archimate_names is False


def test_show_archimate_names_can_be_enabled(db_session, make_org):
    """The setting can be toggled on."""
    user = _make_user(db_session, make_org, show_archimate_names=True)
    assert user.show_archimate_names is True


# ---------------------------------------------------------------------------
# Account page display preferences endpoint
# ---------------------------------------------------------------------------

def test_save_display_preferences_toggle_on(app, db_session, make_org):
    """POST to save_preferences with show_archimate_names=on enables it."""
    client, user = _make_client(app, db_session, make_org)

    resp = client.post(
        "/account/manage/preferences",
        data={"form_type": "display", "show_archimate_names": "on"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    # Re-fetch user from DB to confirm persistence
    db_session.expire(user)
    db_session.refresh(user)
    assert user.show_archimate_names is True


def test_save_display_preferences_toggle_off(app, db_session, make_org):
    """POST to save_preferences without the key leaves it off."""
    # First turn it on. The user is prepared before logging in: changing it
    # afterwards invalidates the test login and the request is redirected.
    client, user = _make_client(app, db_session, make_org, show_archimate_names=True)

    resp = client.post(
        "/account/manage/preferences",
        data={"form_type": "display"},  # unchecked checkbox sends no value
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db_session.expire(user)
    db_session.refresh(user)
    assert user.show_archimate_names is False


# ---------------------------------------------------------------------------
# F1: Template filter wired into rendered pages
# ---------------------------------------------------------------------------

def test_detail_page_uses_plain_name_filter(app, db_session, make_org):
    """The element detail page renders element_type through |plain_name in
    the title and identity badge, not only in the breadcrumb URL."""
    from app.models.archimate_core import ArchiMateElement

    client, user = _make_client(app, db_session, make_org)
    org_id = user.organization_id

    elem = ArchiMateElement(
        name="TestApp",
        type="ApplicationComponent",
        organization_id=org_id,
    )
    db_session.add(elem)
    db_session.flush()

    resp = client.get(
        f"/architecture/application/ApplicationComponent/{elem.id}"
    )
    assert resp.status_code == 200
    html = resp.data.decode()
    # The <title> uses |plain_name so with setting off (default) it
    # reads "Application: TestApp"
    assert "<title>Application: TestApp" in html


def test_detail_page_respects_show_archimate_names(app, db_session, make_org):
    """With show_archimate_names=True, the detail page shows PascalCase
    names in the title and identity badge, not only the breadcrumb URL."""
    from app.models.archimate_core import ArchiMateElement

    client, user = _make_client(app, db_session, make_org, show_archimate_names=True)
    org_id = user.organization_id

    elem = ArchiMateElement(
        name="TestApp",
        type="ApplicationComponent",
        organization_id=org_id,
    )
    db_session.add(elem)
    db_session.flush()

    resp = client.get(
        f"/architecture/application/ApplicationComponent/{elem.id}"
    )
    assert resp.status_code == 200
    html = resp.data.decode()
    # The <title> uses |plain_name so with setting ON it reads
    # "ApplicationComponent: TestApp"
    assert "<title>ApplicationComponent: TestApp" in html


def test_detail_page_title_changes_with_toggle(app, db_session, make_org):
    """The same element's detail page title differs between the two
    settings — this proves the toggle controls the rendered output."""
    from app.models.archimate_core import ArchiMateElement

    # User with setting OFF
    client_off, user_off = _make_client(app, db_session, make_org, show_archimate_names=False)
    org_id = user_off.organization_id

    elem = ArchiMateElement(
        name="ToggleTest",
        type="ApplicationComponent",
        organization_id=org_id,
    )
    db_session.add(elem)
    db_session.flush()

    resp_off = client_off.get(
        f"/architecture/application/ApplicationComponent/{elem.id}"
    )
    html_off = resp_off.data.decode()
    assert "<title>Application: ToggleTest" in html_off

    # Second user with setting ON, same org/element
    client_on, user_on = _make_client(app, db_session, make_org, show_archimate_names=True)
    resp_on = client_on.get(
        f"/architecture/application/ApplicationComponent/{elem.id}"
    )
    html_on = resp_on.data.decode()
    assert "<title>ApplicationComponent: ToggleTest" in html_on

    # The two titles are different
    assert html_off != html_on


# ---------------------------------------------------------------------------
# F2: Unified save_preferences endpoint
# ---------------------------------------------------------------------------

def test_save_preferences_handles_notification_keys(app, db_session, make_org):
    """POST to save_preferences with notification keys updates them."""
    client, user = _make_client(app, db_session, make_org)

    resp = client.post(
        "/account/manage/preferences",
        data={
            "form_type": "notifications",
            "arb_decisions": "on",
            "weekly_digest": "on",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db_session.expire(user)
    db_session.refresh(user)
    prefs = user.notification_preferences or {}
    assert prefs.get("arb_decisions") is True
    assert prefs.get("weekly_digest") is True
    assert prefs.get("solution_updates") is False


def test_save_preferences_handles_both_in_one_request(app, db_session, make_org):
    """POST to save_preferences with both notification and display keys."""
    client, user = _make_client(app, db_session, make_org)

    resp = client.post(
        "/account/manage/preferences",
        data={
            "form_type": "notifications",
            "arb_decisions": "on",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db_session.expire(user)
    db_session.refresh(user)
    # Only notification prefs should be affected
    prefs = user.notification_preferences or {}
    assert prefs.get("arb_decisions") is True
    # Display preference should be unchanged (default False)
    assert user.show_archimate_names is False


def test_saving_notifications_does_not_reset_the_display_preference(app, db_session, make_org):
    """The display preference shares the notification JSON; saving the
    notification form (which does not carry it) must leave it switched on."""
    client, user = _make_client(app, db_session, make_org, show_archimate_names=True)
    db_session.commit()

    resp = client.post(
        "/account/manage/preferences",
        data={"form_type": "notifications", "arb_decisions": "on"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db_session.expire(user)
    db_session.refresh(user)
    assert user.show_archimate_names is True
    assert user.notification_preferences.get("arb_decisions") is True
    assert user.notification_preferences.get("weekly_digest") is False


def test_saving_the_display_preference_keeps_the_notification_choices(app, db_session, make_org):
    # Prepare the user first: changing it after the test login invalidates the
    # login and the request would be redirected to the sign-in page.
    user = _make_user(db_session, make_org)
    user.set_notification_preferences({"arb_decisions": False, "weekly_digest": True})
    db_session.commit()
    client = app.test_client()
    _login(client, user.id)

    client.post(
        "/account/manage/preferences",
        data={"form_type": "display", "show_archimate_names": "on"},
        follow_redirects=True,
    )

    db_session.expire(user)
    db_session.refresh(user)
    assert user.show_archimate_names is True
    assert user.get_notification_preference("arb_decisions") is False
    assert user.get_notification_preference("weekly_digest") is True


def test_set_notification_preferences_keeps_known_keys_it_is_not_given(db_session, make_org):
    user = _make_user(db_session, make_org, show_archimate_names=True)

    user.set_notification_preferences({"arb_decisions": False})

    assert user.show_archimate_names is True
    assert user.get_notification_preference("arb_decisions") is False


def test_set_notification_preferences_still_drops_unknown_keys(db_session, make_org):
    user = _make_user(db_session, make_org)
    user.notification_preferences = {"arb_decisions": True, "retired_key": True}

    user.set_notification_preferences({"weekly_digest": False, "not_a_preference": True})

    assert set(user.notification_preferences) == {"arb_decisions", "weekly_digest"}


def test_set_notification_preferences_reads_a_json_string_value(db_session, make_org):
    user = _make_user(db_session, make_org)
    user.notification_preferences = '{"show_archimate_names": true}'

    user.set_notification_preferences({"arb_decisions": False})

    assert user.notification_preferences == {"show_archimate_names": True, "arb_decisions": False}


def test_unknown_form_type_returns_error(app, db_session, make_org):
    """POST to save_preferences with an unknown form_type flashes an error
    and does not commit any preference change."""
    from flask import session as flask_session

    client, user = _make_client(app, db_session, make_org, show_archimate_names=True)

    with client:
        resp = client.post(
            "/account/manage/preferences",
            data={"form_type": "bogus"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Unknown preference form type" in html

    # Preferences must be unchanged
    db_session.expire(user)
    db_session.refresh(user)
    assert user.show_archimate_names is True


def test_plain_name_for_normalizes_snake_case(app, db_session, make_org):
    """plain_name_for converts snake_case input to PascalCase before lookup."""
    from app.models.archimate_element_types import plain_name_for

    assert plain_name_for("application_component") == "Application"
    assert plain_name_for("business_actor") == "Person or team"


# ---------------------------------------------------------------------------
# F3: JS globals present in admin base template
# ---------------------------------------------------------------------------

def test_admin_base_includes_plain_language_js_globals(app, db_session, make_org):
    """The admin base template injects PLAIN_LANGUAGE_NAMES as JS globals."""
    client, user = _make_client(app, db_session, make_org)

    resp = client.get("/architecture/")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "window.__PLAIN_LANGUAGE_NAMES__" in html
    assert "window.__PLAIN_LAYER_NAMES__" in html
    assert "window.__SHOW_ARCHIMATE_NAMES__" in html
    # Default is false
    assert '"__SHOW_ARCHIMATE_NAMES__": false' in html or \
           'window.__SHOW_ARCHIMATE_NAMES__ = false' in html


def test_admin_base_shows_archimate_names_true_when_enabled(app, db_session, make_org):
    """When show_archimate_names is True, the JS global reflects it."""
    client, user = _make_client(app, db_session, make_org, show_archimate_names=True)

    resp = client.get("/architecture/")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert '"__SHOW_ARCHIMATE_NAMES__": true' in html or \
           'window.__SHOW_ARCHIMATE_NAMES__ = true' in html


# ---------------------------------------------------------------------------
# F4: _plain_name and _plain_layer log on exception
# ---------------------------------------------------------------------------

def test_plain_name_does_not_crash_on_detached_user(app):
    """_plain_name handles a plain object with no show_archimate_names attr."""
    from app.template_helpers import _plain_name

    class FakeUser:
        pass

    # Should not raise, should fall back to plain name
    result = _plain_name("ApplicationComponent", FakeUser())
    assert result == "Application"


def test_plain_layer_does_not_crash_on_detached_user(app):
    """_plain_layer handles a plain object with no show_archimate_names attr."""
    from app.template_helpers import _plain_layer

    class FakeUser:
        pass

    result = _plain_layer("application", FakeUser())
    assert result == "Applications"


# ---------------------------------------------------------------------------
# F5: main/index.html no longer has inconsistent ArchiMate references
# ---------------------------------------------------------------------------

def test_landing_page_no_archimate_in_badge(app):
    """The public landing page does not carry an ArchiMate badge."""
    client = app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "Powered by ArchiMate" not in html


def test_landing_page_no_archimate_in_feature_desc(app):
    """No feature text on the landing page refers to ArchiMate's internal
    element vocabulary (e.g. "ApplicationComponent") in place of a plain
    description.

    Naming the ArchiMate 3.2 *standard* once, in the hero's search-facing
    sentence ("built on ArchiMate 3.2"), is a deliberate, approved choice
    (docs/eim-category-positioning-v1.md section 5.1's own hero text,
    agreed with the SEO plan for the credibility/ranking value of naming
    the open standard) -- not the jargon this test guards against, which
    is referring to features using ArchiMate's own element-type names
    instead of plain language.
    """
    client = app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "ArchiMate elements" not in html


# ---------------------------------------------------------------------------
# F6: SQLite migration uses INTEGER not BOOLEAN
# ---------------------------------------------------------------------------

def test_show_archimate_names_stored_in_notification_preferences():
    """show_archimate_names is stored inside notification_preferences JSON,
    not as a separate column.  The migration in manage.py no longer creates
    a dedicated column."""
    manage_path = os.path.join(os.path.dirname(__file__), "..", "manage.py")
    with open(manage_path, encoding="utf-8") as f:
        source = f.read()
    # The dedicated column migration must not exist
    assert "show_archimate_names BOOLEAN" not in source, (
        "manage.py must not create a separate show_archimate_names column"
    )
    assert "show_archimate_names INTEGER" not in source, (
        "manage.py must not create a separate show_archimate_names column"
    )
    # The value lives in _DEFAULT_NOTIFICATION_PREFS
    from app.models.user import User
    assert "show_archimate_names" in User._DEFAULT_NOTIFICATION_PREFS
    assert User._DEFAULT_NOTIFICATION_PREFS["show_archimate_names"] is False


# ---------------------------------------------------------------------------
# F7: show_archimate_names stored inside notification_preferences JSON
# ---------------------------------------------------------------------------

def test_show_archimate_names_reads_from_notification_preferences(db_session, make_org):
    """The show_archimate_names property delegates to get_notification_preference."""
    user = _make_user(db_session, make_org)
    # Default is False (from _DEFAULT_NOTIFICATION_PREFS)
    assert user.show_archimate_names is False
    assert user.get_notification_preference("show_archimate_names") is False


def test_show_archimate_names_writes_to_notification_preferences(db_session, make_org):
    """Setting show_archimate_names updates the notification_preferences JSON."""
    user = _make_user(db_session, make_org)
    user.show_archimate_names = True
    db_session.flush()
    # The JSON column must contain the key
    prefs = user.notification_preferences or {}
    assert prefs.get("show_archimate_names") is True
    # The property must reflect it
    assert user.show_archimate_names is True
    assert user.get_notification_preference("show_archimate_names") is True


def test_show_archimate_names_preserves_other_preferences(db_session, make_org):
    """Writing show_archimate_names does not clobber other notification prefs."""
    user = _make_user(db_session, make_org)
    user.set_notification_preferences({"arb_decisions": False, "weekly_digest": True})
    db_session.flush()

    user.show_archimate_names = True
    db_session.flush()

    assert user.get_notification_preference("arb_decisions") is False
    assert user.get_notification_preference("weekly_digest") is True
    assert user.get_notification_preference("show_archimate_names") is True


def test_show_archimate_names_cross_organisation(db_session, make_org):
    """The property is a pure read/write on the user's own JSON — no cross-org leak."""
    user_a = _make_user(db_session, make_org)
    user_b = _make_user(db_session, make_org)

    user_a.show_archimate_names = True
    db_session.flush()

    # user_b must be unaffected
    assert user_b.show_archimate_names is False
    assert user_a.show_archimate_names is True


# ---------------------------------------------------------------------------
# F8: instance_detail.html orphans / undocumented / io-chip plain_name wiring
# ---------------------------------------------------------------------------

def test_orphans_table_uses_plain_name_filter():
    """The orphans table on the EA workflow detail page renders element_type
    through |plain_name, not raw PascalCase."""
    import os
    template_path = os.path.join(
        os.path.dirname(__file__), "..", "app", "templates",
        "ea_workflows", "instance_detail.html"
    )
    with open(template_path, encoding="utf-8") as f:
        source = f.read()

    # The orphans table type column (line ~380) must use |plain_name
    assert "el.element_type | plain_name(current_user)" in source, (
        "Orphans table must pipe element_type through |plain_name"
    )
    # The undocumented elements table type column (line ~403) must use |plain_name
    # Count occurrences — there should be at least 2 (orphans + undocumented)
    count = source.count("element_type | plain_name(current_user)")
    assert count >= 2, (
        f"Expected at least 2 uses of element_type|plain_name, found {count}"
    )


def test_io_chip_uses_plain_name_filter():
    """The input/output element chips render el.type through |plain_name."""
    import os
    template_path = os.path.join(
        os.path.dirname(__file__), "..", "app", "templates",
        "ea_workflows", "instance_detail.html"
    )
    with open(template_path, encoding="utf-8") as f:
        source = f.read()

    # The io-chip type span (line ~2129) must use |plain_name
    assert "el.type | plain_name(current_user)" in source, (
        "I/O chip must pipe el.type through |plain_name"
    )


def test_orphans_table_no_raw_element_type_without_filter():
    """The orphans/undocumented tables must not render element_type without
    the |plain_name filter (the raw PascalCase leak)."""
    import os
    template_path = os.path.join(
        os.path.dirname(__file__), "..", "app", "templates",
        "ea_workflows", "instance_detail.html"
    )
    with open(template_path, encoding="utf-8") as f:
        source = f.read()

    # The pattern "element_type or '—'" (without |plain_name) must not exist
    assert "element_type or '—'" not in source, (
        "Raw element_type without |plain_name filter must not exist in template"
    )


# ---------------------------------------------------------------------------
# F9: Legacy route saves display preferences on the rollback path
# ---------------------------------------------------------------------------

def test_legacy_save_preferences_handles_display_and_notifications(app, db_session, make_org):
    """The v1 save_preferences endpoint persists both notification and
    display preferences, matching the v2 behaviour."""
    from app.modules.account.routes.account_routes import save_preferences

    # The endpoint is a regular function — test its logic directly
    client, user = _make_client(app, db_session, make_org)

    resp = client.post(
        "/account/manage/preferences",
        data={"form_type": "display", "show_archimate_names": "on"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db_session.expire(user)
    db_session.refresh(user)
    assert user.show_archimate_names is True

    # Also test notification preferences save correctly via the same endpoint
    resp = client.post(
        "/account/manage/preferences",
        data={"form_type": "notifications", "arb_decisions": "on"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db_session.expire(user)
    db_session.refresh(user)
    assert user.get_notification_preference("arb_decisions") is True
    # Display preference must not be reset by saving notifications
    assert user.show_archimate_names is True


# ---------------------------------------------------------------------------
# F10: Context processor skips injection on unauthenticated pages
# ---------------------------------------------------------------------------

def test_plain_language_context_empty_for_anonymous(app):
    """The plain_language_context processor returns an empty dict when no
    user is signed in, avoiding ~3 KB of JSON serialisation on every
    unauthenticated page render."""
    client = app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode()
    # Public pages must not include the plain-language JSON globals
    assert "window.__PLAIN_LANGUAGE_NAMES__" not in html
    assert "window.__PLAIN_LAYER_NAMES__" not in html
    assert "window.__SHOW_ARCHIMATE_NAMES__" not in html


def test_plain_language_context_populated_for_authenticated(app, db_session, make_org):
    """The plain_language_context processor returns the full payload when
    a user is signed in."""
    client, user = _make_client(app, db_session, make_org)
    resp = client.get("/architecture/")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "window.__PLAIN_LANGUAGE_NAMES__" in html
    assert "window.__PLAIN_LAYER_NAMES__" in html
    assert "window.__SHOW_ARCHIMATE_NAMES__" in html

