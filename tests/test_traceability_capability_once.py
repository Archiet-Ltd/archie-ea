"""DG-031: the traceability pivot lists each capability once, and its
capability count is the number of distinct capabilities modelled, labelled as
such, while /capability-map/ counts the business capabilities in the catalogue.

Org A is seeded with capabilities where one is stored twice (an import and a
sync, one with a differently spelled layer) and one is related to two
elements; org B has its own capabilities that must never appear.
"""

from __future__ import annotations

import html as html_lib
import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org, label):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label,
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    role = Role.query.filter_by(name="Administrator").first()
    if role is not None:
        user.role = role
    db_session.commit()
    return user


def _seed(db_session, org, names, doubled, tag):
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.unified_capability import UnifiedCapability

    elements = {}
    for n in names:
        db_session.add(UnifiedCapability(
            name=f"{n} {tag}", code=f"{tag}-{len(elements)}", organization_id=org.id, level=1))
        el = ArchiMateElement(name=f"{n} {tag}", type="Capability", layer="Strategy",
                              organization_id=org.id)
        db_session.add(el)
        elements[n] = el
    for n in doubled:
        db_session.add(ArchiMateElement(name=f"  {n.upper()} {tag}", type="Capability",
                                        layer="strategy", organization_id=org.id))
    db_session.flush()
    app_a = ArchiMateElement(name=f"App1 {tag}", type="ApplicationComponent",
                             layer="Application", organization_id=org.id)
    app_b = ArchiMateElement(name=f"App2 {tag}", type="ApplicationComponent",
                             layer="Application", organization_id=org.id)
    db_session.add_all([app_a, app_b])
    db_session.flush()
    first = elements[names[0]]
    for app in (app_a, app_b):
        db_session.add(ArchiMateRelationship(
            source_id=app.id, target_id=first.id, type="RealizationRelationship",
            organization_id=org.id))
    db_session.commit()


def test_pivot_lists_each_capability_once_and_is_tenant_scoped(
    app, db_session, make_org, login_as, client
):
    org_a = make_org("dg031-a")
    org_b = make_org("dg031-b")
    _seed(db_session, org_a, ["Inventory Control", "Order Fulfilment", "Billing"],
          ["Inventory Control", "Order Fulfilment"], "AAA")
    _seed(db_session, org_b, ["Other One", "Other Two"], ["Other One"], "BBB")
    login_as(client, _make_user(db_session, org_a, "dg031-user"))

    # The rows the pivot lists (CSV export reads the same query, no page limit).
    export = client.get("/api/archimate/traceability/export?pivot_type=Capability")
    assert export.status_code == 200
    lines = export.data.decode("utf-8").strip().splitlines()[1:]
    names = [ln.split(",")[0].strip().lower() for ln in lines]
    assert sorted(names) == ["billing aaa", "inventory control aaa", "order fulfilment aaa"], names
    assert not any("bbb" in n for n in names)

    # The page: the dropdown count is the distinct figure and names its population.
    page = client.get("/architecture/traceability?pivot_type=Capability")
    assert page.status_code == 200
    text = html_lib.unescape(page.data.decode("utf-8"))
    option = re.search(r"Capability \(([^)]*)\)", text)
    assert option is not None, "Capability pivot option missing"
    assert option.group(1) == "Strategy, 3 capability elements modelled", option.group(1)
    assert "BBB" not in text

    # The capability map counts the catalogue, and says so.
    cmap = client.get("/capability-map/")
    assert cmap.status_code == 200
    ctext = cmap.data.decode("utf-8")
    assert re.search(r'id="unified-cap-count">\s*3\s*<', ctext)
    assert "Business capabilities in the catalogue" in ctext
