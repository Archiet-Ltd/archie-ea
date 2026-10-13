"""Tests over _orphan_link_choice: which connected layer an orphan element
links to, and with what relationship type, once its stored layer value has
been resolved through ArchiMateLayer.canonical the same way
_link_orphan_elements resolves it before looking anything up in
VALID_RELATIONSHIPS. Most of these are pure and need no database; one test
goes through _link_orphan_elements itself, end to end, to prove the two
ArchiMateLayer.canonical call sites inside it are load-bearing rather than
merely exercised indirectly by the pure-function tests above.
"""

from __future__ import annotations

import pytest

from app.models.archimate_core import VALID_RELATIONSHIPS
from app.models.constants import ArchiMateLayer
from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import (
    _orphan_link_choice,
)


@pytest.mark.parametrize(
    "stored_spelling", ["implementation", "Implementation", "implementation_migration"]
)
def test_orphan_on_implementation_migration_links_to_motivation_by_realization(stored_spelling):
    """An orphan Work Package on Implementation & Migration, with one
    connected Motivation element, links by Realization -- for every stored
    spelling of the seventh layer.
    """
    orphan_layer = ArchiMateLayer.canonical(stored_spelling)
    connected_layers = [ArchiMateLayer.canonical("motivation")]

    chosen_layer, relationship_type = _orphan_link_choice(
        orphan_layer, connected_layers, VALID_RELATIONSHIPS
    )

    assert chosen_layer == "motivation"
    assert relationship_type == "Realization"


@pytest.mark.parametrize(
    "orphan_spelling, connected_spelling",
    [
        ("implementation", "implementation_migration"),
        ("implementation_migration", "implementation"),
    ],
)
def test_same_layer_different_spelling_links_by_association(orphan_spelling, connected_spelling):
    """An orphan and a connected element genuinely on the same layer, stored
    under two different spellings of that layer's name, must still meet --
    linking by Association, the same way two rows stored under the identical
    spelling already do.
    """
    orphan_layer = ArchiMateLayer.canonical(orphan_spelling)
    connected_layers = [ArchiMateLayer.canonical(connected_spelling)]

    chosen_layer, relationship_type = _orphan_link_choice(
        orphan_layer, connected_layers, VALID_RELATIONSHIPS
    )

    assert chosen_layer == "implementation_migration"
    assert relationship_type == "Association"


def test_control_untouched_layers_return_todays_result():
    """An orphan on motivation with a connected strategy element -- a
    pairing this task does not touch -- resolves exactly as it does today.
    """
    chosen_layer, relationship_type = _orphan_link_choice(
        "motivation", ["strategy"], VALID_RELATIONSHIPS
    )

    assert chosen_layer == "strategy"
    assert relationship_type == "Association"


def test_no_matrix_entry_either_direction_returns_no_link():
    """An orphan on Implementation & Migration with a connected business
    element has no matrix entry for the pairing and returns no link, as it
    does today.
    """
    chosen_layer, relationship_type = _orphan_link_choice(
        "implementation_migration", ["business"], VALID_RELATIONSHIPS
    )

    assert chosen_layer is None
    assert relationship_type is None


# --- End to end, through _link_orphan_elements itself -----------------------


@pytest.mark.parametrize(
    "org_slug, orphan_type, orphan_layer_stored, connected_type, connected_layer_stored, "
    "bystander_type, bystander_layer_stored, expected_relationship_type",
    [
        pytest.param(
            "orphan-e2e-orphan-side",
            "WorkPackage", "Implementation",
            "Stakeholder", "motivation",
            "Driver", "motivation",
            "realization",
            id="orphan-side-fold-cross-layer-to-motivation",
        ),
        pytest.param(
            "orphan-e2e-connected-side",
            "WorkPackage", "implementation_migration",
            "Deliverable", "Implementation",
            "Gap", "implementation_migration",
            "association",
            id="connected-side-fold-same-layer",
        ),
    ],
)
def test_link_orphan_elements_end_to_end_with_raw_legacy_spelling(
    app, db_session, make_org,
    org_slug, orphan_type, orphan_layer_stored, connected_type, connected_layer_stored,
    bystander_type, bystander_layer_stored, expected_relationship_type,
):
    """Calls SolutionAIOrchestrator()._link_orphan_elements directly -- not
    _orphan_link_choice -- so both ArchiMateLayer.canonical call sites
    inside it are exercised for real, not merely by proxy: the parametrised
    tests above call _orphan_link_choice directly and canonicalise the
    stored spelling themselves before doing so, so they cannot catch a
    regression in _link_orphan_elements's own two call sites.

    The two parameter sets isolate one call site each. The stored spelling
    reaches the function exactly as this codebase's other writers of
    ArchiMateElement.layer would leave it in the ORM object they hold, even
    though the column type lower-cases what is actually persisted and read
    back -- the case survives here only because these are the same
    in-session, already-flushed Python objects the query later returns from
    the identity map, not a fresh read from the row. What both cases prove
    is the WORD fold ("implementation" -> "implementation_migration"), which
    no casing mechanism performs:

    - "orphan-side-fold-cross-layer-to-motivation": the orphan is stored
      "Implementation" and the connected element "motivation" -- a
      genuinely cross-layer pairing, so only the orphan-side call
      (_link_orphan_elements's own read of the orphan's layer) needs to
      fold the word for a link to form at all; the connected-side call
      folds "motivation" to itself either way and proves nothing extra.
    - "connected-side-fold-same-layer": the orphan is stored already
      canonical, "implementation_migration" (so the orphan-side call folds
      nothing), and the connected element is stored "Implementation" -- the
      same layer, spelled the legacy way. Only the connected-side call
      (bucketing connected elements by their canonical layer) makes the two
      elements land in the same bucket; reverting it alone, with the
      orphan-side call left in place, drops the link.
    """
    from flask import g

    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.solution_models import Solution, SolutionArchiMateElement
    from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import (
        SolutionAIOrchestrator,
    )

    org = make_org(org_slug)
    solution = Solution(name="Orphan E2E", organization_id=org.id)
    db_session.add(solution)
    db_session.flush()

    orphan = ArchiMateElement(
        name="Orphan", type=orphan_type, layer=orphan_layer_stored,
        organization_id=org.id,
    )
    connected = ArchiMateElement(
        name="Connected", type=connected_type, layer=connected_layer_stored,
        organization_id=org.id,
    )
    bystander = ArchiMateElement(
        name="Bystander", type=bystander_type, layer=bystander_layer_stored,
        organization_id=org.id,
    )
    db_session.add_all([orphan, connected, bystander])
    db_session.flush()

    # `connected` carries a relationship (to `bystander`, outside the
    # solution) so it is not itself an orphan; `orphan` carries none.
    db_session.add(ArchiMateRelationship(
        type="Association", source_id=connected.id, target_id=bystander.id,
        organization_id=org.id,
    ))
    db_session.add_all([
        SolutionArchiMateElement(solution_id=solution.id, element_id=orphan.id),
        SolutionArchiMateElement(solution_id=solution.id, element_id=connected.id),
    ])
    db_session.commit()

    solution_id = solution.id
    orphan_id, connected_id = orphan.id, connected.id

    with app.test_request_context("/"):
        g.current_org_id = org.id
        SolutionAIOrchestrator()._link_orphan_elements(solution_id)
        db_session.commit()

        new_rel = ArchiMateRelationship.query.filter_by(
            source_id=orphan_id, target_id=connected_id,
        ).first()

    assert new_rel is not None, (
        f"an orphan stored as {orphan_layer_stored!r} must still link to a "
        f"connected element stored as {connected_layer_stored!r}"
    )
    assert new_rel.type == expected_relationship_type
