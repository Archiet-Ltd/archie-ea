"""R1-B38 PR 2: initiative-to-capability links (TB-0050, PB-0056) and the
overlapping-contracts catalogue entry (PB-0225).

A link is both the strategic_initiative_capabilities join row AND a real
ArchiMate "serving" relationship, written through the same
ArchiMateRelationshipService every other R1-B38 link uses -- not a second,
text-only record.
"""
from __future__ import annotations

import uuid

import pytest

from app.modules.architecture.services.initiative_capability_links import (
    InitiativeCapabilityLinkError,
    add_link,
    list_links,
    remove_link,
)


def _archimate_element(db_session, org_id, element_type="BusinessObject"):
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement(
        name=f"El {uuid.uuid4().hex[:6]}", type=element_type, layer="business",
        organization_id=org_id,
    )
    db_session.add(element)
    db_session.flush()
    return element


def _initiative(db_session, org_id):
    """A new StrategicInitiative's own after_insert listener now creates its
    ArchiMateElement automatically -- see test_a_new_initiative_gets_its_
    archimate_element_automatically below."""
    from app.models.strategic import StrategicInitiative

    initiative = StrategicInitiative(name=f"Initiative {uuid.uuid4().hex[:6]}", organization_id=org_id)
    db_session.add(initiative)
    db_session.flush()
    return initiative


def _legacy_initiative_with_no_element(db_session, org_id):
    """A row from before the archimate_element_id listener existed --
    simulated with a raw INSERT, since the ORM constructor now always
    creates one."""
    from sqlalchemy import text

    from app.models.strategic import StrategicInitiative

    name = f"Legacy Initiative {uuid.uuid4().hex[:6]}"
    db_session.execute(
        text(
            "INSERT INTO strategic_initiatives (name, organization_id, revision) "
            "VALUES (:name, :org_id, 1)"
        ),
        {"name": name, "org_id": org_id},
    )
    db_session.flush()
    return StrategicInitiative.query.filter_by(name=name, organization_id=org_id).one()


def _capability(db_session, org_id, *, with_element=True):
    from app.models.business_capabilities import BusinessCapability

    element = _archimate_element(db_session, org_id) if with_element else None
    capability = BusinessCapability(
        name=f"Capability {uuid.uuid4().hex[:6]}", organization_id=org_id,
        archimate_element_id=element.id if element else None,
    )
    db_session.add(capability)
    db_session.flush()
    return capability


class TestInitiativeCapabilityLinks:
    def test_add_link_persists_the_contribution_level(self, app, db_session, make_org, tenant_ctx):
        """Review finding: the join-table UPDATE ran before the
        relationship-append's row was flushed, matched zero rows, and
        silently dropped contribution_level with no error."""
        org = make_org("initiative-cap-contribution")
        initiative = _initiative(db_session, org.id)
        capability = _capability(db_session, org.id)

        with tenant_ctx(org.id):
            result = add_link(initiative.id, capability.id, contribution_level="primary")
            assert result["contribution_level"] == "primary"

            db_session.expire_all()
            links = list_links(initiative.id)
            assert links[0]["contribution_level"] == "primary"

    def test_add_link_creates_both_the_join_row_and_the_relationship(
        self, app, db_session, make_org, tenant_ctx
    ):
        org = make_org("initiative-cap-add")
        initiative = _initiative(db_session, org.id)
        capability = _capability(db_session, org.id)

        with tenant_ctx(org.id):
            result = add_link(initiative.id, capability.id)

        assert result["capability_id"] == capability.id
        db_session.expire_all()
        links = list_links(initiative.id)
        assert [link["capability_id"] for link in links] == [capability.id]

        from app.models import ArchiMateRelationship

        rel = ArchiMateRelationship.query.filter_by(
            source_id=initiative.archimate_element_id,
            target_id=capability.archimate_element_id,
        ).first()
        assert rel is not None
        assert rel.type.lower() == "serving"

    def test_add_link_refuses_a_duplicate(self, app, db_session, make_org, tenant_ctx):
        org = make_org("initiative-cap-dup")
        initiative = _initiative(db_session, org.id)
        capability = _capability(db_session, org.id)
        with tenant_ctx(org.id):
            add_link(initiative.id, capability.id)

            with pytest.raises(InitiativeCapabilityLinkError):
                add_link(initiative.id, capability.id)

    def test_add_link_refuses_a_capability_from_another_organisation(
        self, app, db_session, make_org, tenant_ctx
    ):
        org_a = make_org("initiative-cap-fence-a")
        org_b = make_org("initiative-cap-fence-b")
        initiative = _initiative(db_session, org_a.id)
        capability_b = _capability(db_session, org_b.id)

        with tenant_ctx(org_a.id):
            with pytest.raises(InitiativeCapabilityLinkError):
                add_link(initiative.id, capability_b.id)

    def test_add_link_refuses_when_initiative_has_no_element(
        self, app, db_session, make_org, tenant_ctx
    ):
        org = make_org("initiative-cap-no-element")
        initiative = _legacy_initiative_with_no_element(db_session, org.id)
        capability = _capability(db_session, org.id)

        with tenant_ctx(org.id):
            with pytest.raises(InitiativeCapabilityLinkError):
                add_link(initiative.id, capability.id)

    def test_a_new_initiative_gets_its_archimate_element_automatically(
        self, app, db_session, make_org
    ):
        """This model's own docstring claims ArchiMate 3.2 Strategy Layer
        support; before this PR nothing ever created the element."""
        from app.models.archimate_core import ArchiMateElement

        org = make_org("initiative-auto-element")
        initiative = _initiative(db_session, org.id)

        assert initiative.archimate_element_id is not None
        element = ArchiMateElement.query.get(initiative.archimate_element_id)
        assert element.type == "CourseOfAction"
        assert element.layer == "Strategy"
        assert element.organization_id == org.id

    def test_remove_link_deletes_both_the_join_row_and_the_relationship(
        self, app, db_session, make_org, tenant_ctx
    ):
        org = make_org("initiative-cap-remove")
        initiative = _initiative(db_session, org.id)
        capability = _capability(db_session, org.id)
        with tenant_ctx(org.id):
            add_link(initiative.id, capability.id)
            remove_link(initiative.id, capability.id)

        db_session.expire_all()
        assert list_links(initiative.id) == []
        from app.models import ArchiMateRelationship

        rel = ArchiMateRelationship.query.filter_by(
            source_id=initiative.archimate_element_id,
            target_id=capability.archimate_element_id,
        ).first()
        assert rel is None

    def test_two_organisations_links_never_cross(self, app, db_session, make_org, tenant_ctx):
        org_a = make_org("initiative-cap-isolation-a")
        org_b = make_org("initiative-cap-isolation-b")
        initiative_a = _initiative(db_session, org_a.id)
        capability_a = _capability(db_session, org_a.id)
        initiative_b = _initiative(db_session, org_b.id)
        with tenant_ctx(org_a.id):
            add_link(initiative_a.id, capability_a.id)

        with tenant_ctx(org_b.id):
            assert list_links(initiative_b.id) == []


class TestOverlappingContractsCatalogueEntry:
    def _app_with_contract_and_capability(self, db_session, org_id, capability, *, contract_value=1000.0):
        from app.models.application_capability import ApplicationCapabilityMapping
        from datetime import date

        from app.models.application_portfolio import ApplicationComponent, VendorContract
        from app.models.contract_application import ContractApplication

        app_row = ApplicationComponent(name=f"App {uuid.uuid4().hex[:6]}", organization_id=org_id)
        db_session.add(app_row)
        db_session.flush()
        contract = VendorContract(
            contract_name=f"Contract {uuid.uuid4().hex[:6]}", organization_id=org_id,
            contract_value=contract_value, start_date=date(2026, 1, 1),
        )
        db_session.add(contract)
        db_session.flush()
        db_session.add(ContractApplication(
            contract_id=contract.id, application_id=app_row.id, organization_id=org_id,
        ))
        db_session.add(ApplicationCapabilityMapping(
            application_component_id=app_row.id, business_capability_id=capability.id,
            organization_id=org_id,
        ))
        db_session.flush()
        return app_row, contract

    def test_two_applications_sharing_a_capability_are_grouped_as_overlapping(
        self, app, db_session, make_org
    ):
        from app.modules.intelligence.services.query_catalogue import run_entry

        org = make_org("overlap-contracts")
        capability = _capability(db_session, org.id)
        app1, _ = self._app_with_contract_and_capability(db_session, org.id, capability, contract_value=1000.0)
        app2, _ = self._app_with_contract_and_capability(db_session, org.id, capability, contract_value=500.0)
        db_session.commit()

        result = run_entry("overlapping_contracts", org.id)

        assert result["total"] == 1
        group = result["groups"][0]
        assert group["group"] == capability.name
        app_ids_in_group = {row["application_id"] for row in group["rows"]}
        assert app_ids_in_group == {app1.id, app2.id}
        assert group["combined_contract_value"] == 1500.0

    def test_one_contract_covering_both_overlapping_apps_is_counted_once(
        self, app, db_session, make_org
    ):
        """Review finding: contract_applications is many-to-many, so one
        contract covering both overlapping apps must contribute its value
        once, not once per application it covers."""
        from datetime import date

        from app.models.application_capability import ApplicationCapabilityMapping
        from app.models.application_portfolio import ApplicationComponent, VendorContract
        from app.models.contract_application import ContractApplication
        from app.modules.intelligence.services.query_catalogue import run_entry

        org = make_org("overlap-one-contract")
        capability = _capability(db_session, org.id)

        contract = VendorContract(
            contract_name=f"Shared Contract {uuid.uuid4().hex[:6]}", organization_id=org.id,
            contract_value=1000.0, start_date=date(2026, 1, 1),
        )
        db_session.add(contract)
        db_session.flush()

        app1 = ApplicationComponent(name=f"App {uuid.uuid4().hex[:6]}", organization_id=org.id)
        app2 = ApplicationComponent(name=f"App {uuid.uuid4().hex[:6]}", organization_id=org.id)
        db_session.add_all([app1, app2])
        db_session.flush()
        for a in (app1, app2):
            db_session.add(ContractApplication(
                contract_id=contract.id, application_id=a.id, organization_id=org.id,
            ))
            db_session.add(ApplicationCapabilityMapping(
                application_component_id=a.id, business_capability_id=capability.id,
                organization_id=org.id,
            ))
        db_session.commit()

        result = run_entry("overlapping_contracts", org.id)

        assert result["total"] == 1
        assert result["groups"][0]["combined_contract_value"] == 1000.0

    def test_a_capability_used_by_only_one_application_is_not_an_overlap(
        self, app, db_session, make_org
    ):
        from app.modules.intelligence.services.query_catalogue import run_entry

        org = make_org("overlap-no-overlap")
        capability = _capability(db_session, org.id)
        self._app_with_contract_and_capability(db_session, org.id, capability)
        db_session.commit()

        result = run_entry("overlapping_contracts", org.id)

        assert result["total"] == 0
        assert result["reason"] is not None

    def test_two_organisations_overlap_answers_never_cross(self, app, db_session, make_org):
        from app.modules.intelligence.services.query_catalogue import run_entry

        org_a = make_org("overlap-fence-a")
        org_b = make_org("overlap-fence-b")
        capability_a = _capability(db_session, org_a.id)
        self._app_with_contract_and_capability(db_session, org_a.id, capability_a)
        self._app_with_contract_and_capability(db_session, org_a.id, capability_a)
        db_session.commit()

        result_b = run_entry("overlapping_contracts", org_b.id)
        assert result_b["total"] == 0
