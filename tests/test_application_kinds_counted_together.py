"""Screens that ask "is this an application?" count an Application and an ApplicationComponent alike."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _el(db_session, name, type_, layer="Application"):
    from app.models.archimate_core import ArchiMateElement

    e = ArchiMateElement(name=name, type=type_, layer=layer)
    db_session.add(e)
    db_session.flush()
    return e


def test_broken_chains_checks_both_kinds_of_application_in_one_organisation(make_org, tenant_ctx, db_session):
    from app.modules.intelligence.services.traceability_check_service import broken_chains

    org, other = make_org("trk"), make_org("trk2")
    tag = uuid.uuid4().hex[:6]
    with tenant_ctx(org.id):
        component = _el(db_session, f"Component {tag}", "ApplicationComponent")
        application = _el(db_session, f"Application {tag}", "Application")
    with tenant_ctx(other.id):
        _el(db_session, f"Foreign {tag}", "Application")
    with tenant_ctx(org.id):
        result = broken_chains(org.id)
    by_name = {r["name"]: r for r in result["rows"]}
    assert result["checked"] == 2
    for el in (component, application):
        assert by_name[el.name]["break"] == "no_capability", el.name
    assert f"Foreign {tag}" not in by_name


def test_what_it_changes_counts_both_kinds_of_application():
    from app.modules.solutions_strategic.v2.services.solution_record_service import what_it_changes

    groups = [{"elements": [
        {"type": "ApplicationComponent", "change": "Added", "href": "/a"},
        {"type": "Application", "change": "Added", "href": "/b"},
        {"type": "Node", "change": "Added", "href": None},
    ]}]
    solution = SimpleNamespace(id=-1, vendor_products=[])
    out = what_it_changes(solution, groups)
    assert out["applications"] == 2
    assert out["added"] == 2
