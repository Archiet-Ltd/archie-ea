"""The Implementation and migration layer has one count on the model map and on the layer's own page.

The model map counts the model's elements in the layer. The layer's page counted every element of the layer
for its tab, but listed only the elements whose type is one of the five the layer's form knows, so its header
and Elements card read a smaller number than its own tab. Both now read the same elements, and a record the
layer holds that is not drawn on the model map (a gap entered in the gap register) is counted apart and
named as such.
"""
import json
import uuid

import pytest

FIVE_TYPES = ("WorkPackage", "Deliverable", "ImplementationEvent", "Plateau", "Gap")


@pytest.fixture
def org_user(db_session, make_org):
    from app.models.user import Role, User

    def _make(label):
        org = make_org(label)
        user = User(email="impl-%s@example.com" % uuid.uuid4().hex[:10], first_name="Impl", last_name="Count",
                    organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect")
        user.role = Role.query.filter_by(name="Architect").first()
        db_session.add(user)
        db_session.flush()
        return org, user

    return _make


def seed_implementation_layer(org_id, suffix=""):
    """Twenty elements in the layer: fifteen of the form's five types, three read from a GitHub repository and
    two whose stored type is spelt as a model file wrote it ("Work Package"). Plus one gap entered in the gap
    register, which the layer lists but the model map does not draw."""
    from app.services.archimate_backbone import create_backbone_element
    from app.services.gap_register_service import create_gap

    for n in range(15):
        create_backbone_element(element_type=FIVE_TYPES[n % 5], layer="implementation",
                                name="Change %02d %s" % (n, suffix), organization_id=org_id)
    for n in range(3):
        element = create_backbone_element(element_type="WorkPackage", layer="implementation",
                                          name="Repository work %d %s" % (n, suffix), organization_id=org_id)
        element.source_kind = "connector"
        element.source_label = "GitHub"
        element.source_ref = json.dumps({"system": "github", "repository": "acme/shop"})
    for n in range(2):
        create_backbone_element(element_type="Work Package", layer="implementation",
                                name="Imported work %d %s" % (n, suffix), organization_id=org_id)
    create_gap(org_id, "Register gap %s" % suffix)


def test_the_layer_page_and_the_model_map_count_the_same_elements(app, db_session, org_user, login_as, tenant_ctx):
    mine, me = org_user("implmine")
    theirs, _ = org_user("impltheirs")
    with tenant_ctx(theirs.id):
        seed_implementation_layer(theirs.id, "theirs")
        db_session.flush()
    with tenant_ctx(mine.id):
        seed_implementation_layer(mine.id, "mine")
        db_session.flush()

    client = app.test_client()
    login_as(client, me)
    counts = client.get("/architecture/api/layer/counts").get_json()
    login_as(client, me)
    listing = client.get("/architecture/api/layer/implementation/elements?per_page=100").get_json()
    login_as(client, me)
    model_map = client.get("/archimate/api/model-map").get_json()

    mapped = {c["layer"]: c["count"] for c in model_map["counts"]}["implementation"]
    assert mapped == 20
    # The tab, the header and the Elements card: every record of the layer, the gap register's gap included.
    assert counts["counts"]["implementation"] == 21
    assert listing["pagination"]["total"] == 21
    names = {e["name"] for e in listing["elements"]}
    assert {"Imported work 0 mine", "Imported work 1 mine", "Repository work 2 mine", "Register gap mine"} <= names
    assert not any(name.endswith("theirs") for name in names)
    # What the layer holds beyond the model map is counted apart, so the two figures can be read together.
    assert counts["not_on_model_map"]["implementation"] == 1
    assert counts["counts"]["implementation"] - counts["not_on_model_map"]["implementation"] == mapped


def test_a_type_filter_narrows_the_list_but_not_the_layer_count(app, db_session, org_user, login_as, tenant_ctx):
    mine, me = org_user("implfilter")
    with tenant_ctx(mine.id):
        seed_implementation_layer(mine.id, "f")
        db_session.flush()

    client = app.test_client()
    login_as(client, me)
    listing = client.get("/architecture/api/layer/implementation/elements?element_type=Plateau").get_json()
    login_as(client, me)
    counts = client.get("/architecture/api/layer/counts").get_json()
    assert listing["pagination"]["total"] == 3
    assert {e["element_type"] for e in listing["elements"]} == {"Plateau"}
    assert counts["counts"]["implementation"] == 21
