"""The organisation's own products are loaded into its model once, and never leak into another."""
import json

import pytest

from app import db
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.services import customer_zero_model as model

PRODUCT = {
    "product": "Entelim",
    "as_of": "2026-11-14T00:00:00Z",
    "capabilities": [
        {"id": "gh", "name": "Connect GitHub", "status": "shipped",
         "what_a_person_can_do": "Read your repositories into the model.", "limits": ["GitLab"]},
        {"id": "canvas-export", "name": "Export a business model canvas", "status": "not proven",
         "what_exists": "The public menu mentions it."},
        {"id": "sysml", "name": "Bring your SysML models", "status": "not shipped (early access)",
         "what_exists": "A waiting list."},
    ],
}


def _file(tmp_path, data=None, name="entelim.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data or PRODUCT))
    return path


def _elements(org_id):
    return db.session.execute(
        db.select(ArchiMateElement).where(
            ArchiMateElement.organization_id == org_id, ArchiMateElement.source_ref.like("customer-zero:%")
        )
    ).scalars().all()


def test_running_twice_gives_the_same_elements_and_leaves_another_organisation_alone(app, db_session, make_org, tmp_path):
    org, other = make_org(), make_org()
    path = _file(tmp_path)
    first = model.load_product(path, org.id)
    count = len(_elements(org.id))
    second = model.load_product(path, org.id)
    assert (first["created"], count) == (4, 4)          # the product and three capabilities
    assert (second["created"], second["updated"], second["unchanged"]) == (0, 0, 4)
    assert len(_elements(org.id)) == 4
    assert _elements(other.id) == []
    links = db.session.execute(db.select(ArchiMateRelationship).where(
        ArchiMateRelationship.organization_id == org.id, ArchiMateRelationship.type == "association")).scalars().all()
    assert len(links) == 3
    # Loading the same product into the second organisation does not touch the first.
    model.load_product(path, other.id)
    assert len(_elements(org.id)) == 4 and len(_elements(other.id)) == 4
    assert {e.organization_id for e in _elements(other.id)} == {other.id}


def test_status_limit_and_evidence_are_kept_on_the_element(app, db_session, make_org, tmp_path):
    org = make_org()
    model.load_product(_file(tmp_path), org.id)
    by_key = {(e.custom_properties or {}).get("customer_zero_key"): e for e in _elements(org.id)}
    gh, canvas = by_key["gh"], by_key["canvas-export"]
    assert gh.custom_properties["shipped_status"] == "shipped" and gh.custom_properties["limits"] == ["GitLab"]
    assert canvas.custom_properties["shipped_status"] == "not shipped"
    assert canvas.custom_properties["status_detail"] == "not proven"
    assert "Not shipped (not proven)" in canvas.description
    assert "Not covered: GitLab." in gh.description
    assert gh.source_kind == "import" and gh.source_ref == "customer-zero:entelim:gh"


def test_a_row_removed_from_the_file_is_marked_withdrawn_not_deleted(app, db_session, make_org, tmp_path):
    org, other = make_org(), make_org()
    model.load_product(_file(tmp_path), org.id)
    model.load_product(_file(tmp_path), other.id)
    smaller = dict(PRODUCT, capabilities=PRODUCT["capabilities"][:2])
    result = model.load_product(_file(tmp_path, smaller), org.id)
    assert result["withdrawn"] == 1
    by_key = {(e.custom_properties or {}).get("customer_zero_key"): e for e in _elements(org.id)}
    assert len(by_key) == 4  # the product (no key) and three capabilities, none deleted
    assert by_key["sysml"].custom_properties["shipped_status"] == "withdrawn"
    assert by_key["gh"].custom_properties["shipped_status"] == "shipped"
    again = model.load_product(_file(tmp_path, smaller), org.id)
    assert again["withdrawn"] == 0
    assert all((e.custom_properties or {}).get("shipped_status") != "withdrawn" for e in _elements(other.id))


def test_dry_run_writes_nothing_and_a_bad_file_is_refused(app, db_session, make_org, tmp_path):
    org = make_org()
    assert model.load_product(_file(tmp_path), org.id, dry_run=True)["created"] == 4
    assert _elements(org.id) == []
    bad = _file(tmp_path, {"product": "X", "capabilities": [{"id": "a", "name": "A", "status": "maybe"}]}, "bad.json")
    with pytest.raises(model.ProductFileError):
        model.load_product(bad, org.id)
    with pytest.raises(model.ProductFileError):
        model.load_product(_file(tmp_path), 99999999)
    assert _elements(org.id) == []


def test_the_shipped_entelim_file_loads(app, db_session, make_org):
    org = make_org()
    result = model.load_product(model.DATA_DIRECTORY / "entelim.json", org.id)
    assert result["created"] == 49 and result["product"] == "Entelim"


def test_the_product_is_a_product_and_its_status_is_kept_where_the_status_card_reads_it(app, db_session, make_org, tmp_path):
    org = make_org()
    model.load_product(_file(tmp_path), org.id)
    rows = {e.source_ref: e for e in _elements(org.id)}
    assert rows["customer-zero:entelim"].type == "Product"
    assert rows["customer-zero:entelim"].layer == "Business"
    props = rows["customer-zero:entelim:canvas-export"].custom_properties
    assert props["status_label"] == "Not proven" and props["shipped_status"] == "not shipped"
    assert rows["customer-zero:entelim:sysml"].custom_properties["status_label"] == "Not shipped"


def test_every_page_the_shipped_file_links_to_is_a_page_of_the_product(app):
    paths = {rule.rule for rule in app.url_map.iter_rules() if "GET" in rule.methods}
    linked = [row["page"] for row in json.loads((model.DATA_DIRECTORY / "entelim.json").read_text())["capabilities"]
              if row.get("page")]
    assert linked and all(page in paths or page.rstrip("/") in paths or page + "/" in paths for page in linked), linked
