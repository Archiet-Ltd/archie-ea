"""Element detail page: a person reads words, not database values.

Renders the base element detail page for a seeded Gap (it carries a snake_case enum value, a colour hex and an
auto-generated flag) as an ordinary user and as a platform administrator.
"""
from __future__ import annotations

import re


def _render(app, db_session, make_org, tenant_ctx, *, platform_admin):
    from app.models.implementation_migration import Gap
    from app.models.user import User
    from tests.test_ba_tenant_and_authz import _login

    org = make_org("detail-plain")
    with tenant_ctx(org.id):
        gap = Gap(name="Missing scheduling capability", organization_id=org.id)
        db_session.add(gap)
        db_session.flush()
        user = User(
            email=f"plain-{org.id}-{int(platform_admin)}@example.com",
            organization_id=org.id,
            enterprise_role="enterprise_architect",
            confirmed=True,
            is_platform_admin=platform_admin,
        )
        db_session.add(user)
        db_session.commit()
        client = app.test_client()
        with app.app_context():
            _login(client, user.id)
            response = client.get(f"/architecture/implementation/Gap/{gap.id}")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_ordinary_user_reads_plain_words_only(app, db_session, make_org, tenant_ctx):
    html = _render(app, db_session, make_org, tenant_ctx, platform_admin=False)
    # attribute keys and enum-like values are words
    assert "Gap kind" in html and "Capability shortfall" in html
    assert not re.search(r">\s*(gap_kind|capability_shortfall)\s*<", html)
    assert not re.search(r"\b[a-z]+_[a-z_]+\b\s*</dt>", html)
    # database-facing rows are not shown
    assert "Element ID" not in html
    assert "#6B7280" not in html
    assert "Auto generated" not in html and "Technical details" not in html
    # owner row is present, honest about absence
    assert 'data-testid="element-owner"' in html and "No owner recorded" in html
    assert 'data-testid="element-provenance-asof"' in html


def test_platform_administrator_sees_technical_details(app, db_session, make_org, tenant_ctx):
    html = _render(app, db_session, make_org, tenant_ctx, platform_admin=True)
    assert "Technical details" in html
    assert "Element ID" in html
    assert "#6B7280" in html
    assert "Auto generated" in html
    assert "No owner recorded" in html
