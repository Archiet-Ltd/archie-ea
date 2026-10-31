"""Regression guards for the three demo-morning BLOCKER defects (01 Sep 2026).

1. /enterprise/api/work-packages POST 500 when Create is submitted with only a
   Name filled in — the form serialises empty date/number inputs as "" and
   Postgres rejects `invalid input syntax for type date: ""`.
2. Edit -> Save Changes threw `saveEdit is not a function` — the Alpine template
   called `saveEdit()` but the component method is `saveWorkPackage()`.
3. The "Map Applications" modal's checkboxes / Save Mappings used inline on*=
   handlers, which the strict CSP drops, so nothing selected or persisted. They
   are now data-* attributes dispatched by a delegated change listener.

The JS/CSP defects (2, 3) cannot be exercised by pytest, so they are guarded
structurally: the wiring that was wrong is asserted correct in the shipped files.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.routes.unified_enterprise_routes import _normalise_wp_payload

pytestmark = pytest.mark.usefixtures("db_session")

REPO = Path(__file__).resolve().parent.parent


def _make_user(db_session, make_org):
    from app.models.user import User

    org = make_org("wpblocker")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"wpblocker-{suffix}@example.com",
        first_name="WP",
        last_name="Blocker",
        organization_id=org.id,
        confirmed=True,
    )
    user.password = "Sup3rSecret!23"
    db_session.add(user)
    db_session.flush()
    return org, user


# ── Fix 2: work-package create no longer 500s on empty-string dates ──────────

def test_normalise_wp_payload_blanks_become_null_and_values_parse():
    cleaned = _normalise_wp_payload(
        {
            "name": "x",
            "start_date": "",
            "target_date": "  ",
            "estimated_cost": "",
            "percent_complete": "",
            "level": "2",
        }
    )
    assert cleaned["start_date"] is None
    assert cleaned["target_date"] is None
    assert cleaned["estimated_cost"] is None
    assert cleaned["percent_complete"] is None
    assert cleaned["level"] == 2

    parsed = _normalise_wp_payload(
        {"start_date": "2026-05-01", "estimated_cost": "1200.5", "estimated_effort_hours": "40"}
    )
    from datetime import date

    assert parsed["start_date"] == date(2026, 5, 1)
    assert parsed["estimated_cost"] == 1200.5
    assert parsed["estimated_effort_hours"] == 40


def test_normalise_wp_payload_rejects_bad_date():
    with pytest.raises(ValueError):
        _normalise_wp_payload({"start_date": "not-a-date"})


def test_create_work_package_with_blank_dates_returns_201(app, db_session, make_org, client, login_as):
    _org, user = _make_user(db_session, make_org)
    login_as(client, user)

    resp = client.post(
        "/enterprise/api/work-packages",
        json={"name": "QA-WP-blocker", "priority": "medium", "start_date": "", "target_date": ""},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["status"] == "created"
    assert isinstance(body["id"], int)


def test_create_work_package_requires_name(app, db_session, make_org, client, login_as):
    _org, user = _make_user(db_session, make_org)
    login_as(client, user)
    resp = client.post("/enterprise/api/work-packages", json={"name": "   "})
    assert resp.status_code >= 400


# ── Fix 3 (Edit Save): template calls the method the component actually defines ─

def test_work_packages_template_calls_defined_alpine_method(app):
    template = (REPO / "app/templates/enterprise/work_packages.html").read_text(encoding="utf-8")
    # The bug: template @click="saveEdit()" but no saveEdit method existed.
    assert "saveEdit()" not in template
    # The list is rendered on the server now and each row's Edit opens the
    # work package's own edit page, so Edit leads to a working save rather
    # than an inline Alpine handler.
    assert "url_for('implementation_planning.edit_work_package', work_package_id=wp.id)" in template
    assert "implementation_planning.edit_work_package" in app.view_functions


# ── Fix 1 (Map Applications): CSP-safe delegated wiring, no dead inline handlers ─

def test_capability_map_mapping_modal_uses_delegated_handlers_not_inline():
    js = (REPO / "app/static/js/capability_map/index.js").read_text(encoding="utf-8")
    # Checkbox and settings must be data-* driven (inline on*= is dead under CSP).
    assert 'onchange="toggleApplicationSelection' not in js
    assert 'onchange="updateApplicationMapping' not in js
    assert 'data-cm-change="toggle-app"' in js
    assert 'data-cm-change="update-app-mapping"' in js
    # selectAllFiltered must read the data attribute, not the removed onchange attr.
    assert "getAttribute('onchange')" not in js
    assert "querySelectorAll('input[type=\"checkbox\"][data-app-id]" in js
    # The delegated change listener that makes the above fire.
    assert "CM_CHANGES" in js
