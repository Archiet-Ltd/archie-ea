"""Editing and importing elements: the points an independent review found, each proven on its own.

Similar-name matching treats "%" and "_" as characters and stays inside the organisation; a match says which
layer page the stored element belongs to and whether it was created earlier in the same file; a description
says where it came from from the element's provenance record, not from its opening words; a refusal to delete
names what still uses the element in plain words.
"""
import uuid

from app.services.archimate_import_service import ArchiMateImportService


def _xml(tag, *elements):
    rows = "".join(
        '<element identifier="id-%d" xsi:type="%s"><name xml:lang="en">%s</name></element>' % (i, kind, name)
        for i, (kind, name) in enumerate(elements)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" identifier="id-model-%s">'
        '<name xml:lang="en">M %s</name><elements>%s</elements></model>' % (tag, tag, rows)
    )


def _run(org_id, xml, strategy="skip_duplicates"):
    service = ArchiMateImportService()
    return service.execute_import(service.parse_oef_xml(xml), strategy=strategy, organization_id=org_id)


def _store(org_id, name, kind="Product", layer="Business"):
    from app import db
    from app.models.archimate_core import ArchiMateElement

    row = ArchiMateElement(name=name, type=kind, layer=layer, organization_id=org_id)
    db.session.add(row)
    db.session.flush()
    return row


def test_percent_and_underscore_in_a_stored_name_are_not_wildcards(app, db_session, make_org, tenant_ctx):
    org = make_org("sim")
    with tenant_ctx(org.id):
        _store(org.id, "R_te")
        _store(org.id, "100% plan")
        service = ArchiMateImportService()
        # "rate extra" would match "R_te" if "_" were a wildcard; "1000 plan b" would match "100% plan" if "%" were.
        assert service._similar_existing("rate extra", "Product", org.id) is None
        assert service._similar_existing("1000 plan b", "Product", org.id) is None
        # Words really added to the end still match, in either direction, whatever the case or spacing.
        assert service._similar_existing("r_te  extra", "Product", org.id).name == "R_te"
        assert service._similar_existing("100%", "Product", org.id).name == "100% plan"


def test_similar_name_in_another_organisation_is_not_a_match(app, db_session, make_org, tenant_ctx):
    mine, theirs = make_org("simmine"), make_org("simtheirs")
    with tenant_ctx(theirs.id):
        _store(theirs.id, "Onboarding bundle")
    with tenant_ctx(mine.id):
        assert ArchiMateImportService()._similar_existing("onboarding bundle extended", "Product", mine.id) is None


def test_match_names_the_layer_of_the_stored_element(app, db_session, make_org, tenant_ctx):
    org = make_org("layer")
    tag = uuid.uuid4().hex[:6]
    with tenant_ctx(org.id):
        stored = _store(org.id, "Faster %s" % tag, kind="Goal", layer="Motivation")
        result = _run(org.id, _xml(tag, ("Goal", "Faster %s and wider" % tag)))
        [match] = result["matches"]
        assert match["existing_id"] == stored.id
        assert match["existing_layer"] == "motivation"
        assert match["in_file"] is False


def test_match_against_an_element_created_in_the_same_file_says_so(app, db_session, make_org, tenant_ctx):
    org = make_org("infile")
    tag = uuid.uuid4().hex[:6]
    with tenant_ctx(org.id):
        result = _run(org.id, _xml(tag, ("Product", "Alpha %s" % tag), ("Product", "Alpha %s beta" % tag)))
        [match] = result["matches"]
        assert match["in_file"] is True
        assert match["existing_layer"] == "business"


def _architect(db_session, org):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(email="arch-%s@example.com" % uuid.uuid4().hex[:8], first_name="Arch", last_name="Tester",
                organization_id=org.id, confirmed=True)
    user.role = Role.query.filter_by(name="Architect").one()
    db_session.add(user)
    db_session.flush()
    return user


def _goal_row(db_session, org, description, kind=None, label=None):
    from app.models.archimate_core import ArchiMateElement

    row = ArchiMateElement(name="Goal %s" % uuid.uuid4().hex[:6], type="Goal", layer="Motivation",
                           organization_id=org.id, description=description, source_kind=kind, source_label=label)
    db_session.add(row)
    db_session.flush()
    return row


def test_own_description_starting_inferred_from_is_shown(app, db_session, make_org, client, login_as):
    org = make_org("desc")
    user = _architect(db_session, org)
    row = _goal_row(db_session, org, "Inferred from customer interviews in March")
    login_as(client, user)
    page = client.get("/architecture/motivation/element/%d?ae=1" % row.id).get_data(as_text=True)
    assert "Inferred from customer interviews in March" in page
    assert "No description yet" not in page


def test_description_says_where_it_came_from_from_the_provenance_record(app, db_session, make_org, client,
                                                                     login_as):
    org = make_org("origin")
    user = _architect(db_session, org)
    imported = _goal_row(db_session, org, "Faster onboarding for new customers", kind="import", label="model.xml")
    typed = _goal_row(db_session, org, "Faster onboarding for new customers")
    login_as(client, user)
    page = client.get("/architecture/motivation/element/%d?ae=1" % imported.id).get_data(as_text=True)
    assert 'data-testid="element-description-origin"' in page and "Imported from model.xml." in page
    login_as(client, user)
    page = client.get("/architecture/motivation/element/%d?ae=1" % typed.id).get_data(as_text=True)
    assert 'data-testid="element-description-origin"' not in page


def test_refusal_names_records_in_plain_words(app, db_session, make_org, client, login_as):
    from app.models.archimate_viewpoint import ArchimateElementComment

    org = make_org("refuse")
    user = _architect(db_session, org)
    row = _goal_row(db_session, org, "Kept")
    db_session.add(ArchimateElementComment(element_id=row.id, user_id=user.id, comment_text="Check this"))
    db_session.flush()
    login_as(client, user)
    body = client.get("/architecture/motivation/Goal/%d/delete-check?ae=1" % row.id).get_json()
    assert body["allowed"] is False
    assert body["reason"] == "It is still used by model element comments (1). Remove those links first."
    assert "archimate" not in body["reason"].lower()
