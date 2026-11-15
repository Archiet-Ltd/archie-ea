"""The Value Proposition Canvas is a canvas type of its own whose customer segment and value proposition are the
same model elements the Business Model Canvas uses, offered at sign-up, and never shared across organisations.

Two real organisations in every test.
"""

import uuid

import pytest

from app.config.archimate_viewpoints import CANVAS_TEMPLATES
from app.models.archimate_core import ArchiMateElement
from app.models.business_model import BusinessModelCanvas
from app.modules.account.services import onboarding_service as onboarding
from app.modules.business_model_canvas import model_service as ms

SEGMENT = "Municipal water utilities"
KEY = "value_proposition_canvas"


def _user(db_session, org):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="vpc.%s@example.test" % uuid.uuid4().hex[:8], first_name="Va", last_name="Lue",
        organization_id=org.id, enterprise_role="business_architect", confirmed=True,
    )
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "Sup3r-Secret!pass"
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "org_admin", granted_by_id=user.id)
    db_session.flush()
    return user


def _canvas(db_session, org, canvas_type, name):
    canvas = BusinessModelCanvas(name=name, organization_id=org.id, canvas_type=canvas_type)
    db_session.add(canvas)
    db_session.flush()
    return canvas


@pytest.fixture
def two(db_session, make_org):
    one, other = make_org("vpc-one"), make_org("vpc-two")
    out = {"one": one, "two": other}
    for label, org in (("one", one), ("two", other)):
        out[label + "_bmc"] = _canvas(db_session, org, None, "Model " + label)
        out[label + "_vpc"] = _canvas(db_session, org, KEY, "Proposition " + label)
        out[label + "_user"] = _user(db_session, org)
    return out


def test_the_canvas_has_the_value_map_and_the_customer_profile_boxes():
    boxes = [z["box_key"] for z in CANVAS_TEMPLATES[KEY]["zones"]]
    assert boxes == [
        "customer_segments", "value_propositions", "products_services", "gain_creators", "pain_relievers",
        "customer_jobs", "pains", "gains",
    ]
    assert (KEY, "Value Proposition Canvas") in ms.canvas_types()


def test_a_segment_typed_on_the_proposition_canvas_is_the_one_the_business_model_canvas_holds(db_session, two, tenant_ctx):
    with tenant_ctx(two["one"].id):
        on_bmc = ms.add_entry(two["one_bmc"], "customer_segments", name=SEGMENT)
        on_vpc = ms.add_entry(two["one_vpc"], "customer_segments", name=SEGMENT)
        assert on_vpc.id == on_bmc.id
        assert ArchiMateElement.query.filter_by(organization_id=two["one"].id, name=SEGMENT).count() == 1


def test_a_value_proposition_is_the_element_the_business_model_canvas_holds(db_session, two, tenant_ctx):
    with tenant_ctx(two["one"].id):
        on_bmc = ms.add_entry(two["one_bmc"], "value_propositions", name="Readings you can trust")
        on_vpc = ms.add_entry(two["one_vpc"], "value_propositions", name="Readings you can trust")
        assert on_vpc.id == on_bmc.id


def test_renaming_the_segment_on_one_canvas_renames_it_on_the_other(db_session, two, tenant_ctx):
    with tenant_ctx(two["one"].id):
        el = ms.add_entry(two["one_vpc"], "customer_segments", name=SEGMENT)
        ms.add_entry(two["one_bmc"], "customer_segments", element_id=el.id)
        ms.rename_entry(two["one_vpc"], "customer_segments", el.id, "Regional water boards")
        assert [e.name for e in ms.entries(two["one_bmc"], "customer_segments")] == ["Regional water boards"]
        ms.rename_entry(two["one_bmc"], "customer_segments", el.id, "Municipal utilities")
        assert [e.name for e in ms.entries(two["one_vpc"], "customer_segments")] == ["Municipal utilities"]


def test_a_job_a_pain_and_a_pain_reliever_each_land_in_their_own_box(db_session, two, tenant_ctx):
    with tenant_ctx(two["one"].id):
        canvas = two["one_vpc"]
        ms.add_entry(canvas, "customer_jobs", name="Report to the regulator")
        ms.add_entry(canvas, "pains", name="Sensors drift between audits")
        ms.add_entry(canvas, "pain_relievers", name="Drift alerts")
        assert [(e.name, e.type) for e in ms.entries(canvas, "customer_jobs")] == [("Report to the regulator", "BusinessProcess")]
        assert [e.type for e in ms.entries(canvas, "pains")] == ["Assessment"]
        assert [e.type for e in ms.entries(canvas, "pain_relievers")] == ["Value"]


def test_the_page_lists_the_other_canvases_that_use_the_segment_and_each_link_opens(db_session, two, client, login_as, tenant_ctx):
    with tenant_ctx(two["one"].id):
        el = ms.add_entry(two["one_vpc"], "customer_segments", name=SEGMENT)
        ms.add_entry(two["one_bmc"], "customer_segments", element_id=el.id)
        db_session.commit()
    login_as(client, two["one_user"])
    page = client.get("/business-model/%d" % two["one_vpc"].id)
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert 'data-canvas="value-proposition-canvas"' in html
    assert "/business-model/%d" % two["one_bmc"].id in html
    login_as(client, two["one_user"])
    assert client.get("/business-model/%d" % two["one_bmc"].id).status_code == 200


def test_a_segment_on_no_other_canvas_says_so(db_session, two, client, login_as, tenant_ctx):
    with tenant_ctx(two["one"].id):
        ms.add_entry(two["one_vpc"], "customer_segments", name=SEGMENT)
        db_session.commit()
    login_as(client, two["one_user"])
    html = client.get("/business-model/%d" % two["one_vpc"].id).get_data(as_text=True)
    assert "on no other canvas yet" in html


def test_the_advice_to_add_the_segment_to_the_business_model_canvas_links_to_it(db_session, two, client, login_as, tenant_ctx):
    with tenant_ctx(two["one"].id):
        ms.add_entry(two["one_vpc"], "customer_segments", name=SEGMENT)
        db_session.commit()
    login_as(client, two["one_user"])
    html = client.get("/business-model/%d" % two["one_vpc"].id).get_data(as_text=True)
    assert 'href="/business-model/%d" class="text-primary hover:underline">Add it to the Business Model Canvas</a>' % two["one_bmc"].id in html


def test_with_no_business_model_canvas_the_advice_links_to_where_one_is_created(db_session, make_org, client, login_as, tenant_ctx):
    from app.models.business_model import BusinessModelCanvas

    org = make_org("vpc-no-bmc")
    vpc = BusinessModelCanvas(name="Only proposition", organization_id=org.id, canvas_type=KEY)
    db_session.add(vpc)
    db_session.flush()
    user = _user(db_session, org)
    with tenant_ctx(org.id):
        ms.add_entry(vpc, "customer_segments", name=SEGMENT)
        db_session.commit()
    login_as(client, user)
    html = client.get("/business-model/%d" % vpc.id).get_data(as_text=True)
    assert 'href="/business-model/" class="text-primary hover:underline">Add it to the Business Model Canvas</a>' in html


def test_another_organisations_canvases_are_never_listed_as_sharing_the_segment(db_session, two, client, login_as, tenant_ctx):
    with tenant_ctx(two["two"].id):
        ms.add_entry(two["two_bmc"], "customer_segments", name=SEGMENT)
        db_session.commit()
    with tenant_ctx(two["one"].id):
        ms.add_entry(two["one_vpc"], "customer_segments", name=SEGMENT)
        db_session.commit()
        mine = ms.entries(two["one_vpc"], "customer_segments")
        assert ms.other_canvases_showing(two["one_vpc"], mine) == {}
    with tenant_ctx(two["two"].id):
        assert ArchiMateElement.query.filter_by(organization_id=two["two"].id, name=SEGMENT).count() == 1
    login_as(client, two["one_user"])
    html = client.get("/business-model/%d" % two["one_vpc"].id).get_data(as_text=True)
    assert "/business-model/%d" % two["two_bmc"].id not in html
    assert client.get("/business-model/%d" % two["two_vpc"].id).status_code == 404


def test_the_canvas_is_offered_at_sign_up_and_choosing_it_loads_a_filled_canvas(db_session, make_org, tenant_ctx):
    from app.services.archimate_backbone import create_backbone_element

    chooser, other = make_org("vpc-signup-one"), make_org("vpc-signup-two")
    assert (KEY, "Value Proposition Canvas") in onboarding.canvas_choices()
    with tenant_ctx(chooser.id):
        create_backbone_element(element_type="BusinessActor", layer="business", name=SEGMENT, organization_id=chooser.id)
        db_session.commit()
    onboarding.save_choices(chooser, kind="startup", industry=None, frameworks=[], canvases=[KEY])
    with tenant_ctx(chooser.id):
        onboarding.run_starter_data(chooser.id)
        loaded = BusinessModelCanvas.query.filter_by(organization_id=chooser.id, canvas_type=KEY).one()
        assert [e.name for e in ms.entries(loaded, "customer_segments")] == [SEGMENT]
    with tenant_ctx(other.id):
        assert BusinessModelCanvas.query.filter_by(organization_id=other.id, canvas_type=KEY).count() == 0


def test_the_demonstration_canvas_shares_its_segment_with_the_business_model_canvas(db_session, make_org, tenant_ctx):
    from app.commands import seed_framework_canvases as seed
    from app.commands.seed_demo_company import _seed_canvas_model

    org = make_org("vpc-seed")
    with tenant_ctx(org.id):
        bmc = _canvas(db_session, org, None, "Seed model")
        _seed_canvas_model(org.id, bmc)
        seed.seed(org.id)
        vpc = BusinessModelCanvas.query.filter_by(organization_id=org.id, canvas_type=KEY).one()
        for box in ms.element_boxes(vpc):
            assert ms.entries(vpc, box), box
        segment = ms.entries(vpc, "customer_segments")[0]
        assert segment.name == SEGMENT
        assert segment.id in {e.id for e in ms.entries(bmc, "customer_segments")}
        assert ArchiMateElement.query.filter_by(organization_id=org.id, name=SEGMENT).count() == 1


def test_a_240_character_entry_is_kept_whole(db_session, two, tenant_ctx):
    name = ("Continuous sampling " * 13)[:240].strip()
    with tenant_ctx(two["one"].id):
        el = ms.add_entry(two["one_vpc"], "value_propositions", name=name)
        assert el.name == name and len(el.name) >= 239
        assert ArchiMateElement.query.get(el.id).name == name


def test_a_name_longer_than_the_column_is_refused_and_nothing_is_written(db_session, two, tenant_ctx):
    too_long = "x" * 501
    with tenant_ctx(two["one"].id):
        before = ArchiMateElement.query.filter_by(organization_id=two["one"].id).count()
        with pytest.raises(ms.CanvasError) as err:
            ms.add_entry(two["one_vpc"], "value_propositions", name=too_long)
        assert "500 characters or fewer" in str(err.value)
        assert ArchiMateElement.query.filter_by(organization_id=two["one"].id).count() == before
        kept = ms.add_entry(two["one_vpc"], "value_propositions", name="Short")
        with pytest.raises(ms.CanvasError):
            ms.rename_entry(two["one_vpc"], "value_propositions", kept.id, too_long)
        assert ArchiMateElement.query.get(kept.id).name == "Short"


def test_the_shared_writer_keeps_its_cut_for_callers_that_do_not_ask_otherwise(db_session, make_org):
    from app.services.archimate_backbone import MAX_NAME, create_backbone_element

    org = make_org("vpc-cut")
    el = create_backbone_element(element_type="BusinessActor", layer="business", name="y" * 300, organization_id=org.id)
    assert len(el.name) == MAX_NAME
