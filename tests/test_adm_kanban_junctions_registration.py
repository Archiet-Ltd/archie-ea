"""app/models/adm_kanban_junctions.py defines eight model classes
(CardApplication, System, CardSystem, CardArchiMateElement, CardCapability,
BusinessInitiative, CardInitiative, CardDependency) but app/models/__init__.py
only ever imported CardDependency's module indirectly, via
app/services/adm_phase_validator.py importing it for its own use. Under a
normal `import app.models` (what `create_all()`/`db.Model.metadata` actually
walks), the other seven classes were never registered, so their tables were
never created -- a bare `db.session.query` against any of them raised
UndefinedTable, order-dependent on whether something else happened to import
adm_kanban_junctions first. Observed via CI shard flakiness on
tests/test_security_genome_slice.py.
"""

from __future__ import annotations


def test_every_adm_kanban_junction_table_is_registered_on_the_declarative_base():
    from app import db
    from app.models.adm_kanban_junctions import (
        CardApplication,
        System,
        CardSystem,
        CardArchiMateElement,
        CardCapability,
        BusinessInitiative,
        CardInitiative,
        CardDependency,
    )

    expected_tables = {
        CardApplication.__tablename__,
        System.__tablename__,
        CardSystem.__tablename__,
        CardArchiMateElement.__tablename__,
        CardCapability.__tablename__,
        BusinessInitiative.__tablename__,
        CardInitiative.__tablename__,
        CardDependency.__tablename__,
    }
    registered_tables = set(db.Model.metadata.tables.keys())
    missing = expected_tables - registered_tables
    assert not missing, (
        f"these adm_kanban_junctions tables are not on the declarative base's "
        f"metadata, so create_all() never creates them: {missing}"
    )


def test_card_archimate_element_table_actually_exists_in_the_database(db_session):
    from app.models.adm_kanban_junctions import CardArchiMateElement

    # A bare count against the table -- raises UndefinedTable if create_all()
    # never created it, regardless of row content.
    CardArchiMateElement.query.count()
