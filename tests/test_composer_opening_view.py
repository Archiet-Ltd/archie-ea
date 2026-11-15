"""A viewpoint too large to draw readably opens as one count per layer, and a layer opens on its most connected elements.

Two organisations hold different estates; each only ever sees and counts its own.
"""
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.services import archimate_viewpoint_service as svc


def _estate(db_session, org, application, goals, prefix):
    apps = [ArchiMateElement(name="%s app %02d" % (prefix, i), type="ApplicationComponent", layer="application",
                             organization_id=org.id) for i in range(application)]
    db_session.add_all(apps + [ArchiMateElement(name="%s goal %02d" % (prefix, i), type="Goal", layer="motivation",
                                                organization_id=org.id) for i in range(goals)])
    db_session.flush()
    return apps


def test_large_model_opens_as_a_count_per_layer_for_this_organisation_only(app, db_session, make_org, tenant_ctx):
    org, other = make_org("open-a"), make_org("open-b")
    _estate(db_session, org, application=45, goals=6, prefix="own")
    _estate(db_session, other, application=3, goals=90, prefix="foreign")

    with tenant_ctx(org.id):
        data = svc.get_viewpoint_data("layered", solution_id=None)
    assert data["elements"] == [] and data["relationships"] == []
    assert {row["layer"]: row["count"] for row in data["overview"]} == {"application": 45, "motivation": 6}
    assert data["store_total"] == 51

    with tenant_ctx(other.id):
        data = svc.get_viewpoint_data("layered", solution_id=None)
    assert {row["layer"]: row["count"] for row in data["overview"]} == {"application": 3, "motivation": 90}


def test_a_layer_opens_on_its_most_connected_elements_counting_only_its_own_relationships(app, db_session, make_org, tenant_ctx):
    org, other = make_org("open-c"), make_org("open-d")
    apps = _estate(db_session, org, application=50, goals=0, prefix="own")
    foreign = _estate(db_session, other, application=50, goals=0, prefix="foreign")
    # The last element in the organisation has the most connections: an arbitrary slice would drop it.
    hub = apps[-1]
    for target in apps[:6]:
        db_session.add(ArchiMateRelationship(type="Serving", source_id=hub.id, target_id=target.id, organization_id=org.id))
    # The other organisation connects its first element to everything; that must not lift anything here.
    for target in foreign[1:30]:
        db_session.add(ArchiMateRelationship(type="Serving", source_id=foreign[0].id, target_id=target.id,
                                             organization_id=other.id))
    db_session.flush()

    with tenant_ctx(org.id):
        data = svc.get_viewpoint_data("layered", solution_id=None, layer="application")
    names = [e["name"] for e in data["elements"]]
    assert len(names) == svc.VIEWPOINT_OPENING_LIMIT
    assert names[0] == hub.name
    assert all(n.startswith("own") for n in names)
    assert data["layer_total"] == 50
    assert data["layer_omitted_note"] == "the 40 most connected of 50 application elements are shown"
    assert len(data["relationships"]) == 6


def test_a_small_model_still_opens_drawn(app, db_session, make_org, tenant_ctx):
    org = make_org("open-e")
    _estate(db_session, org, application=3, goals=2, prefix="own")
    with tenant_ctx(org.id):
        data = svc.get_viewpoint_data("layered", solution_id=None)
    assert "overview" not in data and data["total"] == 5


def _extras(db_session, org, prefix):
    kinds = ["Metric", "EvidenceRecord", "Location", "Grouping", "Junction", "OrJunction", "SomethingNew"]
    db_session.add_all([ArchiMateElement(name="%s %s" % (prefix, k), type=k, organization_id=org.id) for k in kinds])
    db_session.flush()


def test_every_overview_card_opens_a_view_with_content(app, db_session, make_org, tenant_ctx):
    org, other = make_org("open-f"), make_org("open-g")
    _estate(db_session, org, application=45, goals=3, prefix="own")
    _extras(db_session, org, "own")
    _estate(db_session, other, application=2, goals=60, prefix="foreign")
    _extras(db_session, other, "foreign")

    with tenant_ctx(org.id):
        overview = svc.get_viewpoint_data("layered", solution_id=None)["overview"]
        counts = {row["layer"]: row["count"] for row in overview}
        # Metric and evidence join the motivation layer; the composite and unmapped types share one card.
        assert counts == {"application": 45, "motivation": 5, "composite": 5}
        assert [row["title"] for row in overview if row["layer"] == "composite"] == ["Composite and other elements"]
        for row in overview:
            assert row["layer"] in svc.VALID_LAYER_KEYS
            opened = svc.get_viewpoint_data("layered", solution_id=None, layer=row["layer"])
            assert opened["total"] == min(row["count"], svc.VIEWPOINT_OPENING_LIMIT), row
            assert all(e["name"].startswith("own") for e in opened["elements"])

    with tenant_ctx(other.id):
        counts = {row["layer"]: row["count"] for row in svc.get_viewpoint_data("layered", solution_id=None)["overview"]}
        assert counts == {"application": 2, "motivation": 62, "composite": 5}


def test_the_other_layers_note_counts_only_other_layers(app, db_session, make_org, tenant_ctx):
    org, other = make_org("open-h"), make_org("open-i")
    _estate(db_session, org, application=45, goals=35, prefix="own")
    _estate(db_session, other, application=45, goals=10, prefix="foreign")

    with tenant_ctx(org.id):
        data = svc.get_viewpoint_data("layered", solution_id=None, layer="application")
    assert data["total"] == 40
    assert data["layer_omitted_note"] == "the 40 most connected of 45 application elements are shown"
    assert data["omitted_note"] == "35 elements in other layers are not shown"
    assert data["omitted"] == 35 and data["store_total"] == 80

    with tenant_ctx(org.id):
        data = svc.get_viewpoint_data("layered", solution_id=None, layer="motivation")
    assert data["total"] == 35 and "layer_omitted_note" not in data
    assert data["omitted_note"] == "45 elements in other layers are not shown"
