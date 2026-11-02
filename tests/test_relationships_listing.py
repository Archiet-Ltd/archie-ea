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
    """Walking the pages reaches every relationship of the organisation (a page holds at most 20) and none of another's."""
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
            listed = set()
            for page in (1, 2, 3):
                html = c.get("/architecture/relationships?page=%d" % page).get_data(as_text=True)
                on_page = {int(n) for n in re.findall(r'<tr[^>]*id="relationship-(\d+)"', html)}
                assert len(on_page) <= 20
                listed |= on_page
    assert set(made[ours.id]) <= listed, "rows missing: %s" % sorted(set(made[ours.id]) - listed)
    assert not listed & set(made[theirs.id])


def _many_relationships(db_session, org, count):
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    elements = [ArchiMateElement(name="P%d-%d" % (org.id, n), type="ApplicationComponent",
                                 layer="Application", organization_id=org.id) for n in range(count + 1)]
    db_session.add_all(elements)
    db_session.commit()
    rels = [ArchiMateRelationship(type="serving", source_id=elements[n].id, target_id=elements[n + 1].id)
            for n in range(count)]
    db_session.add_all(rels)
    db_session.commit()
    return [rel.id for rel in rels]


@pytest.mark.usefixtures("db_session")
def test_a_citation_opens_the_page_holding_its_row_marked_selected(app, db_session, make_org, tenant_ctx):
    """45 relationships, page size 20: a citation to one on page 3 opens page 3, never more than a page of rows."""
    import re

    from app.models.user import User

    ours, theirs = make_org("rel-focus-ours"), make_org("rel-focus-theirs")
    with tenant_ctx(theirs.id):
        theirs_ids = _many_relationships(db_session, theirs, 3)
    with tenant_ctx(ours.id):
        ids = sorted(_many_relationships(db_session, ours, 45))
        user = User(email="rl-focus-%d@example.com" % ours.id, organization_id=ours.id,
                    enterprise_role="enterprise_architect", confirmed=True)
        db_session.add(user)
        db_session.commit()

        from tests.test_ba_tenant_and_authz import _login
        c = app.test_client()
        with app.app_context():
            _login(c, user.id)
            cited = ids[44]
            html = c.get("/architecture/relationships?focus=%d" % cited).get_data(as_text=True)
            plain = c.get("/architecture/relationships").get_data(as_text=True)
            other = c.get("/architecture/relationships?focus=%d" % theirs_ids[0]).get_data(as_text=True)
            second = c.get("/enterprise/architecture/relationships?focus=%d" % cited).get_data(as_text=True)
    listed = [int(n) for n in re.findall(r'<tr[^>]*id="relationship-(\d+)"', html)]
    assert len(listed) <= 20, "a page holds at most one page of rows, got %d" % len(listed)
    assert cited in listed
    assert re.search(r'<tr[^>]*id="relationship-%d"[^>]*data-state="selected"|<tr[^>]*data-state="selected"[^>]*id="relationship-%d"' % (cited, cited), html)
    assert len(re.findall(r'data-state="selected"', html)) == 1
    assert "Relationships 41 to 45 of 45" in html
    assert html.count(">Cited<") == 1 and ">Cited<" not in plain
    assert len(re.findall(r'<tr[^>]*id="relationship-', plain)) == 20
    assert 'data-state="selected"' not in plain
    assert not {int(n) for n in re.findall(r'id="relationship-(\d+)"', other)} & set(theirs_ids)
    assert 'id="relationship-%d"' % cited in second


@pytest.mark.usefixtures("db_session")
def test_another_organisations_relationship_id_as_focus_is_ignored(app, db_session, make_org, tenant_ctx):
    """A citation id that belongs to another organisation finds nothing: page 1, no row marked."""
    from app.modules.architecture.services.relationship_listing import relationships_page

    ours, theirs = make_org("rel-foreign-ours"), make_org("rel-foreign-theirs")
    with tenant_ctx(ours.id):
        ours_ids = _many_relationships(db_session, ours, 45)
    with tenant_ctx(theirs.id):
        theirs_ids = _many_relationships(db_session, theirs, 3)
        # their row is now loaded in this session, as it is when one worker serves both organisations
        from app.models.archimate_core import ArchiMateRelationship
        assert db_session.get(ArchiMateRelationship, max(theirs_ids)).type is not None
    with tenant_ctx(ours.id):
        rows, pagination = relationships_page(focus_id=max(theirs_ids))
    assert pagination.page == 1
    assert pagination.total == 45
    assert not any(r["cited"] for r in rows)
    assert {r["id"] for r in rows} <= set(ours_ids)


@pytest.mark.usefixtures("db_session")
def test_a_page_past_the_end_redirects_to_the_last_page(app, db_session, make_org, tenant_ctx):
    """Page 99 of 45 relationships opens page 3, and the count line never reads a range past the total."""
    from app.models.user import User

    ours = make_org("rel-pastend-ours")
    with tenant_ctx(ours.id):
        _many_relationships(db_session, ours, 45)
        user = User(email="rl-pastend-%d@example.com" % ours.id, organization_id=ours.id,
                    enterprise_role="enterprise_architect", confirmed=True)
        db_session.add(user)
        db_session.commit()

        from tests.test_ba_tenant_and_authz import _login
        c = app.test_client()
        with app.app_context():
            _login(c, user.id)
            past = c.get("/architecture/relationships?page=99")
            landed = c.get("/architecture/relationships?page=99", follow_redirects=True).get_data(as_text=True)
    assert past.status_code == 302
    assert "page=3" in past.headers["Location"]
    assert "Relationships 41 to 45 of 45" in landed
    assert "No relationships yet" not in landed
