"""F-05(a), Capgemini walkthrough: the element detail page's Relationships
tab was read-only with no way to add a relationship, even though the create
API and valid-types API already worked for real element ids. These tests pin
the backend chain the new "Add relationship" control drives (search -> valid
types -> create), and that the detail page actually renders the control.
"""

import pytest


@pytest.mark.usefixtures("db_session")
def test_detail_page_renders_add_relationship_control(app, db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement
    from app.models.user import User

    org = make_org("add-rel-ui")
    with tenant_ctx(org.id):
        el = ArchiMateElement(name="MuleSoft Integration Layer", type="ApplicationComponent",
                               layer="Application", organization_id=org.id)
        db_session.add(el)
        db_session.commit()
        el_id = el.id

        user = User(email=f"ar-{org.id}@example.com", organization_id=org.id,
                    enterprise_role="enterprise_architect", confirmed=True)
        db_session.add(user)
        db_session.commit()

        from tests.test_ba_tenant_and_authz import _login
        c = app.test_client()
        with app.app_context():
            _login(c, user.id)
            resp = c.get(f"/architecture/application/ApplicationComponent/{el_id}")
            assert resp.status_code == 200
            html = resp.get_data(as_text=True)
            assert "Add relationship" in html
            assert f"addRelationshipForm({el_id})" in html


@pytest.mark.usefixtures("db_session")
def test_search_valid_types_create_chain_round_trips(app, db_session, make_org, tenant_ctx):
    """The exact three-call chain the new UI drives: search for a target,
    fetch valid relationship types for the pair, create the relationship,
    then read it back independently on the source element's detail page."""
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.user import User

    org = make_org("add-rel-chain")
    with tenant_ctx(org.id):
        source = ArchiMateElement(name="MuleSoft Integration Layer", type="ApplicationComponent",
                                   layer="Application", organization_id=org.id)
        target = ArchiMateElement(name="SCADE Plant Control", type="ApplicationComponent",
                                   layer="Application", organization_id=org.id)
        db_session.add_all([source, target])
        db_session.commit()
        source_id, target_id = source.id, target.id

        user = User(email=f"arc-{org.id}@example.com", organization_id=org.id,
                    enterprise_role="enterprise_architect", confirmed=True)
        db_session.add(user)
        db_session.commit()

        from tests.test_ba_tenant_and_authz import _login
        c = app.test_client()
        with app.app_context():
            _login(c, user.id)

            search = c.get("/archimate/api/elements/search?q=SCADE")
            assert search.status_code == 200
            names = [e["name"] for e in search.get_json()["data"]]
            assert "SCADE Plant Control" in names

            types = c.get(f"/archimate/api/valid-relationship-types?source_id={source_id}&target_id={target_id}")
            assert types.status_code == 200
            valid_types = types.get_json()["valid_types"]
            assert len(valid_types) > 0

            create = c.post("/archimate/api/relationships", json={
                "source_element_id": source_id,
                "target_element_id": target_id,
                "relationship_type": valid_types[0],
            })
            assert create.status_code == 201, create.get_data(as_text=True)

            # Independent read: the relationship persisted, and the detail
            # page's own query finds it.
            rel = db_session.query(ArchiMateRelationship).filter_by(
                source_id=source_id, target_id=target_id
            ).first()
            assert rel is not None
            assert rel.type == valid_types[0]

            detail = c.get(f"/architecture/application/ApplicationComponent/{source_id}")
            assert detail.status_code == 200
            assert "SCADE Plant Control" in detail.get_data(as_text=True)


@pytest.mark.usefixtures("db_session")
def test_impact_view_names_a_person_with_no_name_by_their_email(app, db_session, make_org, tenant_ctx):
    """A hand-drawn link whose maker has no first or last name still opens the model page."""
    from app.models.archimate_core import ArchiMateElement
    from app.models.user import User
    from app.modules.intelligence.services import query_service

    org = make_org("origin-noname")
    with tenant_ctx(org.id):
        maker = User(email=f"noname-{org.id}@example.com", organization_id=org.id, confirmed=True)
        db_session.add(maker)
        db_session.flush()
        rows = [{"relation": {"kind": "explicit", "provenance": {
            "_person_ids": [maker.id], "_created_at": None, "text": "Origin not recorded"}}}]
        query_service._attach_origins(rows, org.id)
        assert rows[0]["relation"]["provenance"]["text"] == f"Drawn by {maker.email}"
