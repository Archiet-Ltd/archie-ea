"""Organisation-chosen business keys are unique per organisation.

Each organisation overrides a system-default governance gate by name and
numbers its own vendor contracts, so two organisations must be able to hold
the same gate name or contract number, while one organisation still cannot
hold two. ``flask reconcile-schema`` brings a database created with the old
platform-wide rules into line. A vendor contract is also mirrored into the
architecture model as an ArchiMate Contract.
"""

from datetime import date

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.usefixtures("db_session")


def _gate(db_session, name):
    from app.models.governance_gates import GovernanceGate

    gate = GovernanceGate(gate_name=name, min_completeness=70)
    db_session.add(gate)
    db_session.flush()
    return gate


def _contract(db_session, org_id, name, number):
    from app.models.application_portfolio import VendorContract

    contract = VendorContract(organization_id=org_id, contract_name=name,
                              contract_number=number, start_date=date(2026, 1, 1))
    db_session.add(contract)
    db_session.flush()
    return contract


def test_two_organisations_may_use_the_same_gate_name(db_session, make_org, tenant_ctx):
    from app.models.governance_gates import GovernanceGate

    first, second = make_org("gate-a"), make_org("gate-b")
    with tenant_ctx(first.id):
        mine = _gate(db_session, "arb_submission_shared")
    with tenant_ctx(second.id):
        theirs = _gate(db_session, "arb_submission_shared")
        visible = GovernanceGate.query.filter_by(gate_name="arb_submission_shared").all()
    assert mine.organization_id == first.id and theirs.organization_id == second.id
    assert [g.id for g in visible] == [theirs.id]


def test_one_organisation_cannot_hold_the_same_gate_name_twice(db_session, make_org, tenant_ctx):
    org = make_org("gate-dup")
    with tenant_ctx(org.id):
        _gate(db_session, "arb_submission_dup")
        with pytest.raises(IntegrityError):
            with db_session.begin_nested():
                _gate(db_session, "arb_submission_dup")


def test_contract_numbers_are_unique_per_organisation(db_session, make_org, tenant_ctx):
    first, second = make_org("contract-a"), make_org("contract-b")
    with tenant_ctx(first.id):
        _contract(db_session, first.id, "Their MSA", "MSA-UNIQ-001")
        with pytest.raises(IntegrityError):
            with db_session.begin_nested():
                _contract(db_session, first.id, "Second MSA", "MSA-UNIQ-001")
    with tenant_ctx(second.id):
        other = _contract(db_session, second.id, "Our MSA", "MSA-UNIQ-001")
    assert other.id is not None


def test_a_new_contract_is_mirrored_as_an_archimate_contract(db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement

    org = make_org("contract-mirror")
    with tenant_ctx(org.id):
        contract = _contract(db_session, org.id, "Mirror MSA", None)
        element = db_session.get(ArchiMateElement, contract.archimate_element_id)
        assert (element.type, element.layer, element.name, element.organization_id) == (
            "Contract", "Business", "Mirror MSA", org.id)
        contract.contract_name = "Mirror MSA renamed"
        db_session.flush()
        db_session.expire(element)
        assert element.name == "Mirror MSA renamed"


def _reconcile(existing_tables, dry_run=False):
    from app.commands.reconcile_schema import _ensure_tenant_scoped_unique_keys

    added, failed = [], []
    _ensure_tenant_scoped_unique_keys(dry_run=dry_run, existing_tables=existing_tables,
                                      added=added, failed=failed)
    return added, failed


def test_reconcile_schema_replaces_the_platform_wide_rules(db_session):
    conn = db_session.connection()
    # Put both tables back the way older databases have them. Everything here,
    # including the row changes that let the old rules be re-created on a
    # shared database, is rolled back at teardown.
    conn.execute(text("DELETE FROM governance_gates"))
    conn.execute(text("UPDATE vendor_contracts SET contract_number = NULL"))
    conn.execute(text("ALTER TABLE governance_gates DROP CONSTRAINT IF EXISTS uq_governance_gates_org_gate_name"))
    conn.execute(text("ALTER TABLE governance_gates DROP CONSTRAINT IF EXISTS governance_gates_gate_name_key"))
    conn.execute(text("ALTER TABLE governance_gates ADD CONSTRAINT governance_gates_gate_name_key UNIQUE (gate_name)"))
    conn.execute(text("ALTER TABLE vendor_contracts DROP CONSTRAINT IF EXISTS uq_vendor_contracts_org_contract_number"))
    conn.execute(text("DROP INDEX IF EXISTS ix_vendor_contracts_contract_number"))
    conn.execute(text("CREATE UNIQUE INDEX ix_vendor_contracts_contract_number ON vendor_contracts (contract_number)"))

    tables = {"governance_gates", "vendor_contracts"}

    planned, failed = _reconcile(tables, dry_run=True)
    assert failed == [] and len(planned) == 2
    added, failed = _reconcile(tables)
    assert failed == []
    assert [a.split(" ::")[0] for a in added] == [
        "constraint.governance_gates.uq_governance_gates_org_gate_name",
        "constraint.vendor_contracts.uq_vendor_contracts_org_contract_number",
    ]
    insp = inspect(conn)
    gate_uniques = {u["name"] for u in insp.get_unique_constraints("governance_gates")}
    assert gate_uniques == {"uq_governance_gates_org_gate_name"}
    contract_indexes = {ix["name"]: ix["unique"] for ix in insp.get_indexes("vendor_contracts")}
    assert contract_indexes["ix_vendor_contracts_contract_number"] is False
    assert _reconcile(tables) == ([], [])
