"""
Enterprise Genome — DATA slice: builder + Article 30 RoPA emitter.

Pins the three invariants the slice is sold on:
  * real element names render into the RoPA table (no fabrication),
  * every row resolves to a real, seeded ArchiMate element id (structural
    provenance, non-optional),
  * two builds of an unchanged model are byte-identical (determinism, 0-LLM).

Uses the shared fixtures in tests/conftest.py (make_org, db_session).
"""
import re

import pytest

from app.models.models import ArchiMateElement, ArchiMateRelationship
from app.modules.codegen.services.genome_data_ropa_emitter import render_ropa_table
from app.modules.codegen.services.genome_data_slice import (
    ProvenanceError,
    build_data_genome_slice,
)


def _seed_data_estate(db_session, org_id):
    """A small, honest data estate: 3 information objects + 2 systems + access edges."""

    def elem(name, type_):
        e = ArchiMateElement(name=name, type=type_, layer=None)
        e.organization_id = org_id
        db_session.add(e)
        db_session.flush()
        return e

    # Two systems that process data.
    crm = elem("CRM Platform", "application_component")
    billing = elem("Billing Service", "application_component")

    # Three information objects — mixed type spellings on purpose (snake + Camel).
    customer = elem("Customer Record", "data_object")
    invoice = elem("Invoice", "DataObject")           # CamelCase must normalise
    contract = elem("Service Contract", "business_object")

    # One object carrying real governance metadata, to prove it renders when present.
    invoice.custom_properties = {
        "purpose": "Billing and revenue recognition",
        "lawful_basis": "contract",
        "retention": "P7Y",
        "data_categories": ["financial", "contact"],
    }
    db_session.flush()

    def access(system, obj, mode):
        r = ArchiMateRelationship(type="access", source_id=system.id, target_id=obj.id)
        r.access_mode = mode
        r.organization_id = org_id
        db_session.add(r)
        db_session.flush()
        return r

    access(crm, customer, "readwrite")
    access(billing, invoice, "read")
    access(billing, contract, "read")

    return {
        "objects": [customer, invoice, contract],
        "systems": [crm, billing],
    }


def test_real_element_names_render(db_session, make_org):
    org = make_org("genome-data")
    seeded = _seed_data_estate(db_session, org.id)

    slice_dict = build_data_genome_slice(org.id, session=db_session)
    html = str(render_ropa_table(slice_dict))

    for obj in seeded["objects"]:
        assert obj.name in html, f"{obj.name!r} missing from RoPA output"
    # A processing system name renders too (access edge resolved to a real system).
    assert "CRM Platform" in html
    # Real governance metadata surfaces where modelled.
    assert "contract" in html and "P7Y" in html
    # Honest sparseness: an object with no lawful basis shows an em dash, not a guess.
    assert "—" in html


def test_every_row_resolves_to_a_real_element_id(db_session, make_org):
    org = make_org("genome-data")
    seeded = _seed_data_estate(db_session, org.id)
    real_ids = {e.id for e in seeded["objects"]}

    slice_dict = build_data_genome_slice(org.id, session=db_session)

    # Builder side: every activity carries a non-null, real source element id.
    assert len(slice_dict["processing_activities"]) == len(seeded["objects"])
    for act in slice_dict["processing_activities"]:
        eid = act["provenance"]["archimate_element_id"]
        assert eid in real_ids
        assert act["provenance"]["origin"] == "element"
        assert act["provenance"]["archimate_type"]  # non-empty

    # Emitter side: every rendered row's data-element-id is a real seeded id.
    html = str(render_ropa_table(slice_dict))
    rendered_ids = {int(m) for m in re.findall(r'data-element-id="(\d+)"', html)}
    assert rendered_ids == real_ids


def test_two_builds_are_byte_identical(db_session, make_org):
    org = make_org("genome-data")
    _seed_data_estate(db_session, org.id)

    first = build_data_genome_slice(org.id, session=db_session)
    second = build_data_genome_slice(org.id, session=db_session)

    assert first["spec_hash"] == second["spec_hash"]
    assert first == second
    # And the rendered artifact is byte-identical, not just the IR.
    assert str(render_ropa_table(first)) == str(render_ropa_table(second))


def test_empty_org_is_honest_not_fabricated(db_session, make_org):
    org = make_org("genome-data-empty")
    slice_dict = build_data_genome_slice(org.id, session=db_session)
    assert slice_dict["processing_activities"] == []
    html = str(render_ropa_table(slice_dict))
    assert "No information objects modelled" in html


def test_provenance_error_is_defined():
    # The build-error type exists and is a ValueError subclass (fail-closed contract).
    assert issubclass(ProvenanceError, ValueError)


def test_org_id_is_required(db_session):
    with pytest.raises(ValueError):
        build_data_genome_slice(None, session=db_session)


# ---------------------------------------------------------------------------
# Two-organisation isolation
# ---------------------------------------------------------------------------


def _seed_with_supplier(db_session, org_id, label="default"):
    """Seed a data estate with a data object, two systems, and a supplier."""
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    def elem(name, type_):
        e = ArchiMateElement(name=name, type=type_, layer=None)
        e.organization_id = org_id
        db_session.add(e)
        db_session.flush()
        return e

    crm = elem(f"CRM System {label}", "application_component")
    billing = elem(f"Billing System {label}", "application_component")
    customer = elem(f"Customer Record {label}", "data_object")

    def access(system, obj, mode):
        r = ArchiMateRelationship(type="access", source_id=system.id, target_id=obj.id)
        r.access_mode = mode
        r.organization_id = org_id
        db_session.add(r)
        db_session.flush()
        return r

    access(crm, customer, "readwrite")
    access(billing, customer, "read")

    # Add a flow relationship between billing and the data object
    flow = ArchiMateRelationship(type="flow", source_id=billing.id, target_id=customer.id)
    flow.organization_id = org_id
    db_session.add(flow)
    db_session.flush()

    # Add a supplier via VendorContract
    vendor = VendorOrganization(name=f"Data Processor Inc {label}")
    db_session.add(vendor)
    db_session.flush()

    # Need ApplicationComponent rows for the contracts
    crm_app = ApplicationComponent(
        name=f"CRM System App {label}", organization_id=org_id,
        archimate_element_id=crm.id,
    )
    billing_app = ApplicationComponent(
        name=f"Billing System App {label}", organization_id=org_id,
        archimate_element_id=billing.id,
    )
    db_session.add_all([crm_app, billing_app])
    db_session.flush()

    contract = VendorContract(
        contract_name=f"CRM Support Contract {label}",
        organization_id=org_id,
        application_id=crm_app.id,
        vendor_id=vendor.id,
        start_date=__import__("datetime").date(2026, 1, 1),
    )
    db_session.add(contract)
    db_session.flush()

    return {
        "objects": [customer],
        "systems": [crm, billing],
        "suppliers": [vendor],
    }


def test_slice_lists_only_own_org_activities(db_session, make_org):
    """Two organisations: the slice for org A contains only A's activities."""
    org_a = make_org("ropa-a")
    org_b = make_org("ropa-b")
    _seed_with_supplier(db_session, org_a.id, "org-a")
    _seed_with_supplier(db_session, org_b.id, "org-b")

    slice_a = build_data_genome_slice(org_a.id, session=db_session)
    slice_b = build_data_genome_slice(org_b.id, session=db_session)

    # Each org sees only its own activities (the data object + any auto-created
    # elements like contract ArchiMate elements)
    a_names = {a["name"] for a in slice_a["processing_activities"]}
    b_names = {a["name"] for a in slice_b["processing_activities"]}
    assert "Customer Record org-a" in a_names
    assert "Customer Record org-b" in b_names
    assert "Customer Record org-a" not in b_names
    assert "Customer Record org-b" not in a_names
    # The element ids differ because each org has its own elements
    a_data_ids = {a["provenance"]["archimate_element_id"] for a in slice_a["processing_activities"]
                  if a["name"] == "Customer Record org-a"}
    b_data_ids = {a["provenance"]["archimate_element_id"] for a in slice_b["processing_activities"]
                  if a["name"] == "Customer Record org-b"}
    assert a_data_ids.isdisjoint(b_data_ids)


def test_slice_includes_suppliers_and_flows(db_session, make_org):
    """A processing activity includes its linked suppliers and flows."""
    org = make_org("ropa-suppliers")
    _seed_with_supplier(db_session, org.id, "supplier-test")

    slice_dict = build_data_genome_slice(org.id, session=db_session)
    # Find the data object activity (there may also be auto-created contract elements)
    activities = [a for a in slice_dict["processing_activities"]
                  if a["name"] == "Customer Record supplier-test"]
    assert len(activities) == 1
    activity = activities[0]

    # Systems are present
    assert len(activity["systems"]) == 2
    system_names = {s["name"] for s in activity["systems"]}
    assert "CRM System supplier-test" in system_names
    assert "Billing System supplier-test" in system_names

    # Suppliers are present
    assert len(activity["suppliers"]) >= 1
    supplier_names = {s["name"] for s in activity["suppliers"]}
    assert "Data Processor Inc supplier-test" in supplier_names

    # Flows are present
    assert len(activity["flows"]) >= 1
    flow_names = {f["name"] for f in activity["flows"]}
    assert "Billing System supplier-test" in flow_names


def test_slice_emitter_shows_suppliers_and_flows(db_session, make_org):
    """The rendered RoPA table includes supplier and flow columns."""
    org = make_org("ropa-emitter")
    _seed_with_supplier(db_session, org.id, "emitter-test")

    slice_dict = build_data_genome_slice(org.id, session=db_session)
    html = str(render_ropa_table(slice_dict))

    # The table has the new column headers
    assert "Suppliers" in html
    assert "Flows" in html
    # Supplier names render
    assert "Data Processor Inc emitter-test" in html
    # Flow names render
    assert "Billing System emitter-test" in html
