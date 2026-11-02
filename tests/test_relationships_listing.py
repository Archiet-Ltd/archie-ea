"""F-05(b), Capgemini walkthrough: /architecture/relationships showed "20 rows
of bare numeric IDs with a — type" — reproducible from TWO independent causes
on the two routes that share this template: one queried an abandoned legacy
table (empty everywhere), the other queried the canonical table but fed the
template attributes (rel.source_element, rel.relationship_type) that
ArchiMateRelationship never declares, so Jinja silently fell back to the bare
id for both. These tests pin the fix: real element names, on both routes.
"""

import pytest


@pytest.mark.usefixtures("db_session")
def test_relationships_listing_shows_real_names_not_bare_ids(app, db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.user import User

    org = make_org("rel-listing")
    with tenant_ctx(org.id):
        source = ArchiMateElement(name="MuleSoft Integration Layer", type="ApplicationComponent",
                                   layer="Application", organization_id=org.id)
        target = ArchiMateElement(name="SCADE Plant Control", type="ApplicationComponent",
                                   layer="Application", organization_id=org.id)
        db_session.add_all([source, target])
        db_session.commit()

        rel = ArchiMateRelationship(type="serving", source_id=source.id, target_id=target.id)
        db_session.add(rel)
        db_session.commit()

        user = User(email=f"rl-{org.id}@example.com", organization_id=org.id,
                    enterprise_role="enterprise_architect", confirmed=True)
        db_session.add(user)
        db_session.commit()

        from tests.test_ba_tenant_and_authz import _login
        c = app.test_client()
        with app.app_context():
            _login(c, user.id)

            resp = c.get("/architecture/relationships")
            assert resp.status_code == 200
            html = resp.get_data(as_text=True)
            assert "MuleSoft Integration Layer" in html
            assert "SCADE Plant Control" in html
            assert "serving" in html

            resp2 = c.get("/enterprise/architecture/relationships")
            assert resp2.status_code == 200
            html2 = resp2.get_data(as_text=True)
            assert "MuleSoft Integration Layer" in html2
            assert "SCADE Plant Control" in html2
            assert "serving" in html2


@pytest.mark.usefixtures("db_session")
def test_every_relationship_of_the_organisation_is_listed_and_none_of_another(app, db_session, make_org, tenant_ctx):
    """A citation links to ``#relationship-<id>``: the row must be on the page however many relationships exist."""
    import re

    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.user import User

    ours, theirs = make_org("rel-all-ours"), make_org("rel-all-theirs")
    made = {}
    for org, count in ((ours, 25), (theirs, 1)):
        with tenant_ctx(org.id):
            elements = [ArchiMateElement(name="E%d-%d" % (org.id, n), type="ApplicationComponent",
                                         layer="Application", organization_id=org.id) for n in range(count + 1)]
            db_session.add_all(elements)
            db_session.commit()
            rels = [ArchiMateRelationship(type="serving", source_id=elements[n].id, target_id=elements[n + 1].id)
                    for n in range(count)]
            db_session.add_all(rels)
            db_session.commit()
            made[org.id] = [rel.id for rel in rels]
    with tenant_ctx(ours.id):
        user = User(email="rl-all-%d@example.com" % ours.id, organization_id=ours.id,
                    enterprise_role="enterprise_architect", confirmed=True)
        db_session.add(user)
        db_session.commit()

        from tests.test_ba_tenant_and_authz import _login
        c = app.test_client()
        with app.app_context():
            _login(c, user.id)
            html = c.get("/architecture/relationships").get_data(as_text=True)
    listed = {int(n) for n in re.findall(r'id="relationship-(\d+)"', html)}
    assert set(made[ours.id]) <= listed, "rows missing: %s" % sorted(set(made[ours.id]) - listed)
    assert not listed & set(made[theirs.id])
