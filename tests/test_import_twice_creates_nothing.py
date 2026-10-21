"""DG-108: importing the same file twice creates nothing the second time."""
import os
import re

import pytest

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "archiet-model-v1.oef.xml")


def _xml():
    with open(FIXTURE, encoding="utf-8") as fh:
        return fh.read()


def _run(xml, org_id):
    from app.services.archimate_import_service import ArchiMateImportService

    service = ArchiMateImportService()
    return service.execute_import(
        service.parse_oef_xml(xml), strategy="skip_duplicates", organization_id=org_id
    )


def _counts(org_id):
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    return (
        ArchiMateElement.query.filter_by(organization_id=org_id).count(),
        ArchiMateRelationship.query.filter_by(organization_id=org_id).count(),
    )


def _with_new_relationship(xml):
    ids = re.findall(r'<element identifier="([^"]+)"', xml)
    assert len(ids) >= 2
    new = (
        '<relationship identifier="id-dg108-new" xsi:type="Association" '
        'source="%s" target="%s"/>\n' % (ids[0], ids[1])
    )
    return xml.replace("</relationships>", new + "</relationships>", 1)


def test_second_import_creates_nothing(app, db_session, make_org, tenant_ctx):
    org = make_org("dg108a")
    with tenant_ctx(org.id):
        first = _run(_xml(), org.id)
        after_first = _counts(org.id)
        second = _run(_xml(), org.id)
        assert first["relationships_created"] > 0
        assert second["created"] == 0
        assert second["relationships_created"] == 0
        assert _counts(org.id) == after_first


def test_a_genuinely_new_relationship_is_created_once(app, db_session, make_org, tenant_ctx):
    org = make_org("dg108b")
    with tenant_ctx(org.id):
        _run(_xml(), org.id)
        before = _counts(org.id)
        changed = _with_new_relationship(_xml())
        second = _run(changed, org.id)
        assert second["created"] == 0
        assert second["relationships_created"] == 1
        assert _counts(org.id) == (before[0], before[1] + 1)
        third = _run(changed, org.id)
        assert third["relationships_created"] == 0


def test_second_organisation_gets_the_full_set_and_first_is_untouched(
    app, db_session, make_org, tenant_ctx
):
    org_a, org_b = make_org("dg108c"), make_org("dg108d")
    with tenant_ctx(org_a.id):
        first = _run(_xml(), org_a.id)
        a_counts = _counts(org_a.id)
    with tenant_ctx(org_b.id):
        second = _run(_xml(), org_b.id)
        b_counts = _counts(org_b.id)
    with tenant_ctx(org_a.id):
        assert _counts(org_a.id) == a_counts
    assert second["relationships_created"] == first["relationships_created"]
    assert second["created"] == first["created"]
    assert b_counts == a_counts
