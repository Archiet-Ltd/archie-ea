"""framework_management/extension_dashboard.html used to hardcode a JavaScript
demo object (`getExtensionData`) keyed by URL slug, listing invented extension
packs such as 'Predictive analytics and insights', 'Digital Twin: Virtual
factory modeling', 'AI-powered quality control' and 'IoT Connectivity', plus a
fixed `lastUpdated` of 2024-01-08. None of it was backed by data — the page
rendered the same fabricated catalogue regardless of what, if anything, was
actually registered in the `framework_extensions` table.

The route now looks up the real `FrameworkExtension` row for the requested
slug and renders only what that row actually holds; when no row matches, the
page says so instead of inventing one.
"""

import json

from app.models.framework_configuration import FrameworkExtension

FABRICATED_STRINGS = [
    "Predictive analytics and insights",
    "Digital Twin",
    "Virtual factory modeling",
    "AI-powered quality control",
    "IoT Connectivity",
    "Industrial IoT integration",
    "2024-01-08",
    "2024-01-10",
    "2024-01-12",
    "Automated Quality",
    "Supply Chain Optimization",
]


def _make_platform_admin(db_session, org):
    from app.models.user import Role, User

    user = User(
        email=f"platform-admin-{org.id}@ext.test",
        organization_id=org.id,
        enterprise_role="platform_admin",
        confirmed=True,
        is_platform_admin=True,
    )
    db_session.add(user)
    db_session.flush()

    Role.insert_roles()
    role = Role.query.filter_by(name="Administrator").first()
    user.role = role
    db_session.flush()
    return user


def test_unregistered_extension_shows_honest_empty_state(app, db_session, make_org, client, login_as):
    """No FrameworkExtension row matches this slug — the page must say so, not invent one."""
    org = make_org("ext-empty")
    user = _make_platform_admin(db_session, org)
    login_as(client, user)

    resp = client.get("/framework-management/extensions/manufacturing-advanced")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    for fabricated in FABRICATED_STRINGS:
        assert fabricated not in body, f"fabricated demo string leaked into the page: {fabricated!r}"

    assert "is registered" in body
    assert "Nothing is installed or available" in body
    # No actions offered against a record that does not exist.
    assert "Activate Extension" not in body
    assert 'id="extensionStatus"' not in body


def test_registered_extension_renders_real_fields_only(app, db_session, make_org, client, login_as):
    """A real FrameworkExtension row renders its own fields — no fabricated catalogue entries."""
    org = make_org("ext-real")
    user = _make_platform_admin(db_session, org)
    login_as(client, user)

    extension = FrameworkExtension(
        extension_name="Manufacturing Advanced",
        extension_code="MFG_ADVANCED",
        extension_description="Real extension row used to verify honest rendering.",
        extension_type="industry",
        extension_version="9.9.9",
        provider="Acme Systems",
        target_framework="Unified_Manufacturing_Excellence",
        compatible_versions=json.dumps(["2.0", "2.1"]),
        dependencies=json.dumps(["Core Framework v2.0+"]),
        license_type="commercial",
        status="active",
        download_count=17,
        active_installations=3,
        user_rating=4.1,
        additional_capabilities=json.dumps(["Batch scheduling export"]),
    )
    db_session.add(extension)
    db_session.flush()

    resp = client.get("/framework-management/extensions/manufacturing-advanced")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    for fabricated in FABRICATED_STRINGS:
        assert fabricated not in body, f"fabricated demo string leaked into the page: {fabricated!r}"

    # Real fields from the row are rendered.
    assert "9.9.9" in body
    assert "Acme Systems" in body
    assert "commercial" in body
    assert "Batch scheduling export" in body
    assert "2.0, 2.1" in body
    assert "Core Framework v2.0+" in body
    assert "17" in body
    assert "Activate Extension" in body


def test_registered_extension_with_no_capabilities_shows_honest_features_state(
    app, db_session, make_org, client, login_as
):
    """A real row with no `additional_capabilities` gets an honest 'no features' state, not invented ones."""
    org = make_org("ext-nofeat")
    user = _make_platform_admin(db_session, org)
    login_as(client, user)

    extension = FrameworkExtension(
        extension_name="Digital Transformation",
        extension_code="DIGITAL_TRANSFORM",
        extension_type="technology",
        status="active",
    )
    db_session.add(extension)
    db_session.flush()

    resp = client.get("/framework-management/extensions/digital-transformation")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    for fabricated in FABRICATED_STRINGS:
        assert fabricated not in body

    assert "No features documented" in body
    assert "no registered capabilities yet" in body
