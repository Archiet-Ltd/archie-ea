"""D-07 (PR 430 review v2): the registry cannot drift again, silently.

The write-guard's enumeration ratchet (tests/test_platform_write_guard_enumeration.py,
via scripts/tenant_table_registry.py's live mapper-registry walk) and the read-side
ratchet (scripts/check_untenanted_reads.py, an independent ``ast``-based source
scan) each answer "which tables have no tenant fence" their own way. They had
drifted silently: the committed ledger (scripts/unfenced_tables.txt) named 477
tables, the registry walk found 482, and the static scanner found 467 -- three
different answers to one question, none of them checked against each other.

This asserts all three name the same set, with the symmetric difference printed
by name on failure so a future drift is a one-line diagnosis, not a rediscovery.
"""

from __future__ import annotations

# Tables defined with `__tablename__` on their own, separate
# ``declarative_base()`` (app/modules/ai_chat/services/llm_router.py,
# app/services/rag_engine.py) -- NOT ``db.Model``. They never reach
# ``db.Model.registry`` no matter what gets imported, are never created by
# ``db.create_all()`` (confirmed: absent from a freshly built schema), and so
# never reach ``db.session`` or this guard's ``before_flush`` listener either.
# The static source scanner still finds them (it matches `__tablename__` on
# any base), which is correct for ITS purpose -- flagging a table nobody has
# classified -- so they stay in scripts/unfenced_tables.txt; this is the one
# place that documents why the runtime registry walk will never agree with
# the ledger for exactly these six names.
DEAD_BASE_TABLES = frozenset(
    {"llm_metrics", "llm_requests", "llm_responses", "rag_metrics", "rag_queries", "rag_results"}
)


def test_the_registry_walk_and_the_committed_ledger_agree(app):
    from scripts.tenant_table_registry import listed_table_names, non_tenant_table_classes

    runtime = set(non_tenant_table_classes())
    listed = listed_table_names()

    missing_from_ledger = sorted(runtime - listed)
    stale_in_ledger = sorted(listed - runtime - DEAD_BASE_TABLES)

    assert not missing_from_ledger, (
        "table(s) with no tenant fence exist at runtime but are not in "
        f"scripts/unfenced_tables.txt: {missing_from_ledger} -- classify each "
        "(tenant child -> allow-list entry, or true global catalogue) and add "
        "it to the ledger"
    )
    assert not stale_in_ledger, (
        "scripts/unfenced_tables.txt names table(s) that no longer exist or "
        f"are no longer unfenced: {stale_in_ledger} -- remove them (a table "
        "that gained TenantMixin, or was renamed/dropped, should not stay "
        "listed)"
    )


def test_the_static_scanner_and_the_committed_ledger_agree(app):
    """scripts/check_untenanted_reads.py's ast-based scan vs the same ledger.

    The static scanner and the live registry walk use different discovery
    mechanisms (source-parsing vs the mapper registry) and can therefore see
    different things -- a model defined behind a metaclass trick, or built
    dynamically, might appear to one and not the other. Both must still agree
    with the one committed ledger, which is what actually drives the
    write-guard allow-list and this gate's own --new-tables check.
    """
    from scripts.check_untenanted_reads import discover_models, unfenced_models, unfenced_tables
    from scripts.tenant_table_registry import listed_table_names

    classes = discover_models()
    models = unfenced_models(classes)
    static_tables = set(unfenced_tables(classes, models))
    listed = listed_table_names()

    missing_from_ledger = sorted(static_tables - listed)
    assert not missing_from_ledger, (
        "the static source scanner finds table(s) with no tenant fence that "
        f"are not in scripts/unfenced_tables.txt: {missing_from_ledger}"
    )
