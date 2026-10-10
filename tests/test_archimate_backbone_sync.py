"""Every motivation entity must be ABLE to join the ArchiMate backbone.

CLAUDE.md: "ArchiMate is the backbone, not a view ... A plain textarea is not an
acceptable substitute -- the field IS the element."

The `archimate-backbone` gate counted 53 creation paths that never synced. What
the count hid is that roughly half of them COULD NOT HAVE: Risk,
SolutionDriver, SolutionGoal, SolutionConstraint and SolutionRisk had no
archimate column at all, and SolutionRequirement's archimate_requirement_id
points at the enterprise `requirements` table, not at an element. A convention
cannot be followed where the schema has no place to record compliance.

These tests pin the structural precondition, so the situation cannot return: the
mapping is complete, and every model in it can actually store its link.
"""

import importlib.util
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _checker_motivation_set():
    """The entity set the gate enforces, read from the gate itself."""
    path = os.path.join(REPO, "scripts", "check_archimate_backbone.py")
    spec = importlib.util.spec_from_file_location("_backbone_checker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.MOTIVATION)


def test_the_mapping_covers_exactly_what_the_gate_enforces():
    """A type the gate flags but the helper cannot map is an unfixable finding."""
    from app.services.archimate_backbone import ELEMENT_TYPES

    gate = _checker_motivation_set()
    mapped = set(ELEMENT_TYPES)
    assert not (gate - mapped), (
        "the gate flags these types but archimate_backbone.ELEMENT_TYPES cannot "
        "map them, so the finding could never be closed: %s" % sorted(gate - mapped)
    )


def test_every_mapped_motivation_model_can_store_its_link(app):
    """The precondition that was missing for six of thirteen models."""
    from app import db
    from app.services.archimate_backbone import ELEMENT_TYPES

    with app.app_context():
        by_name = {}
        for mapper in db.Model.registry.mappers:
            by_name.setdefault(mapper.class_.__name__, mapper.class_)

        missing = []
        for type_name in sorted(ELEMENT_TYPES):
            model = by_name.get(type_name)
            if model is None:
                continue  # not mapped in this build; nothing can create one
            if "archimate_element_id" not in model.__table__.c:
                missing.append("%s (%s)" % (type_name, model.__table__.name))

    assert not missing, (
        "these motivation models have nowhere to record their ArchiMate "
        "element, so their creation paths cannot join the backbone at any "
        "price: %s" % ", ".join(missing)
    )


@pytest.mark.parametrize("layer_expected", ["Motivation", "Implementation", "Business"])
def test_every_mapping_names_a_real_archimate_layer(layer_expected):
    from app.services.archimate_backbone import ELEMENT_TYPES

    layers = {layer for _, layer in ELEMENT_TYPES.values()}
    assert layers <= {"Motivation", "Implementation", "Business"}, (
        "unexpected ArchiMate layer in the mapping: %s" % sorted(layers)
    )
    assert layer_expected in layers


def test_a_risk_joins_the_backbone_when_synced(db_session, make_org):
    """End to end on the model that had no link column until now.

    sync_archimate_element creates the element before any flush, and the
    backbone's before_flush listener is idempotent and creates nothing extra.
    Only one ArchiMateElement row exists after the flush.

    Uses the shared fixtures from tests/conftest.py -- db_session runs inside a
    transaction that is always rolled back, so this cannot leave residue in the
    shared test database.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.models.risk import Risk
    from app.services.archimate_backbone import sync_archimate_element

    org = make_org("backbone")
    risk = Risk(
        organization_id=org.id,
        title="Unencrypted PII at rest",
        description="Customer records stored without disk encryption.",
        likelihood=4,
        impact=5,
    )
    db_session.add(risk)

    # sync creates the element before any flush — exercises sync's own path.
    element = sync_archimate_element(risk, session=db_session)
    assert element is not None
    assert element.type == "Assessment"
    assert element.layer == "Motivation"
    assert element.organization_id == org.id

    db_session.flush()

    # After flush the risk has its element linked.
    assert risk.archimate_element_id == element.id
    # Exactly one element for this org — no duplicate from the listener.
    assert db_session.query(ArchiMateElement).filter_by(
        organization_id=org.id, name="Unencrypted PII at rest"
    ).count() == 1

    # Idempotent: sync_archimate_element returns None when already linked.
    assert sync_archimate_element(risk, session=db_session) is None


def test_an_unnamed_motivation_row_raises_rather_than_skipping(db_session, make_org):
    """Silently skipping is what produced a backbone with holes in it."""
    from app.models.risk import Risk
    from app.services.archimate_backbone import sync_archimate_element

    org = make_org("unnamed")
    risk = Risk(organization_id=org.id, title="", description="", likelihood=1, impact=1)
    with pytest.raises(ValueError, match="no name/title"):
        sync_archimate_element(risk, session=db_session)


def test_application_interface_metadata_not_in_element_types():
    """ApplicationInterfaceMetadata has no NAME_FIELDS column, so
    sync_archimate_element can never resolve a name for it -- it is not a
    valid entry point for this type. Task 02 calls create_backbone_element
    directly instead (see test_create_backbone_element_for_interface_metadata
    below), which is the actual seam this type uses."""
    from app.services.archimate_backbone import ELEMENT_TYPES

    assert "ApplicationInterfaceMetadata" not in ELEMENT_TYPES


def test_create_backbone_element_for_interface_metadata_has_provenance(db_session, make_org):
    """The seam Task 02 will use: create_backbone_element called directly
    with element_type="ApplicationInterface", layer="Application" -- not via
    sync_archimate_element/ELEMENT_TYPES -- must still carry a source_model
    default even though no provenance dict was passed."""
    from app.services.archimate_backbone import create_backbone_element

    org = make_org("interface-metadata-seam")
    element = create_backbone_element(
        element_type="ApplicationInterface",
        layer="Application",
        name="SAP S/4 Order Interface",
        organization_id=org.id,
        session=db_session,
    )

    assert element.type == "ApplicationInterface"
    assert element.layer == "Application"
    assert element.custom_properties["source_model"] == "ApplicationInterface"


def test_create_backbone_element_returns_a_flushed_element(db_session, make_org):
    from app.services.archimate_backbone import create_backbone_element

    org = make_org("backbone-element")
    element = create_backbone_element(
        element_type="ApplicationInterface",
        layer="Application",
        name="Order Interface",
        description="SAP order sync interface",
        organization_id=org.id,
        session=db_session,
        provenance={"source_model": "ApplicationInterfaceMetadata"},
    )

    assert element is not None
    assert element.id is not None
    assert element.type == "ApplicationInterface"
    assert element.layer == "Application"
    assert element.scope == "enterprise"
    assert element.organization_id == org.id
    assert element.custom_properties["source_model"] == "ApplicationInterfaceMetadata"


def test_create_backbone_element_truncates_a_long_name(db_session, make_org):
    from app.services.archimate_backbone import MAX_NAME, create_backbone_element

    org = make_org("backbone-truncate")
    long_name = "X" * 150
    element = create_backbone_element(
        element_type="ApplicationInterface",
        layer="Application",
        name=long_name,
        organization_id=org.id,
        session=db_session,
    )

    assert len(element.name) <= MAX_NAME
    assert element.name.endswith("…")
    assert element.custom_properties["source_name"] == long_name


def test_create_backbone_element_raises_on_blank_name(db_session, make_org):
    from app.services.archimate_backbone import create_backbone_element

    org = make_org("backbone-blank-name")
    with pytest.raises(ValueError):
        create_backbone_element(
            element_type="ApplicationInterface",
            layer="Application",
            name="",
            organization_id=org.id,
            session=db_session,
        )


def test_create_backbone_element_raises_on_missing_organization_id(db_session):
    from app.services.archimate_backbone import create_backbone_element

    with pytest.raises(ValueError):
        create_backbone_element(
            element_type="ApplicationInterface",
            layer="Application",
            name="Order Interface",
            organization_id=None,
            session=db_session,
        )


def test_create_backbone_element_raises_on_zero_organization_id(db_session):
    """Postgres serial PKs in this codebase start at 1; 0 is never a real
    organization id and is rejected explicitly rather than hitting a
    foreign-key failure later."""
    from app.services.archimate_backbone import create_backbone_element

    with pytest.raises(ValueError):
        create_backbone_element(
            element_type="ApplicationInterface",
            layer="Application",
            name="Order Interface",
            organization_id=0,
            session=db_session,
        )


def test_create_backbone_element_raises_valueerror_on_non_string_name(db_session, make_org):
    """A non-str name (e.g. an int accidentally passed through) must raise
    ValueError, not AttributeError from calling .strip() on a non-string."""
    from app.services.archimate_backbone import create_backbone_element

    org = make_org("backbone-non-string-name")
    with pytest.raises(ValueError):
        create_backbone_element(
            element_type="ApplicationInterface",
            layer="Application",
            name=12345,
            organization_id=org.id,
            session=db_session,
        )


def test_sync_archimate_element_unchanged_for_existing_mapped_type(db_session, make_org):
    """The refactor to call create_backbone_element must be non-breaking.

    sync_archimate_element creates the element before any flush; the
    before_flush listener sees it already linked and creates nothing extra.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.models.implementation_migration import WorkPackage
    from app.services.archimate_backbone import sync_archimate_element

    org = make_org("backbone-workpackage")
    wp = WorkPackage(
        organization_id=org.id,
        name="Migrate legacy invoicing",
        description="Move invoicing off the legacy mainframe.",
    )
    db_session.add(wp)

    # sync creates the element before any flush.
    element = sync_archimate_element(wp, session=db_session)
    assert element is not None
    assert element.type == "WorkPackage"
    assert element.layer == "Implementation"
    assert element.organization_id == org.id

    db_session.flush()

    # After flush the link is set and exactly one element exists.
    assert wp.archimate_element_id == element.id
    assert db_session.query(ArchiMateElement).filter_by(
        organization_id=org.id, name="Migrate legacy invoicing"
    ).count() == 1

    # Idempotent: sync_archimate_element returns None when already linked.
    assert sync_archimate_element(wp, session=db_session) is None
