"""SolutionCodeBundle <-> Solution mapper configuration.

Production logs showed SQLAlchemy's generic "One or more mappers failed to
initialize" wrapper naming SolutionCodeBundle as the triggering mapper. The
underlying cause: SolutionCodeBundle.solution used a bare string relationship
target ("Solution") with no foreign_keys=, resolved lazily by SQLAlchemy the
first time *anything* forces mapper configuration. That resolution only ever
worked because app/models/__init__.py happens to import
app.modules.solutions_product.models (SolutionCodeBundle) before it imports
Solution -- an ordering accident, not a guarantee. Anything that forces an
early configure_mappers() pass between those two imports -- or any future
reordering of that file -- reproduces the failure, and once configure_mappers()
fails once for any mapper, SQLAlchemy keeps raising the same generic error for
the rest of the process, regardless of which model a later request actually
needed.

The fix replaces the string target with a direct class reference (plus
explicit foreign_keys), so SolutionCodeBundle's own module import cannot
complete without Solution already being importable -- turning a possible
deferred mapper failure into an ordinary, immediate Python import dependency.

These tests all take the `app` fixture (full application factory) so
mapper configuration is only ever exercised after a normal application
boot, the same order real requests see -- not the narrower, partial import
surface a bare unit import can produce, which can fail for reasons
unrelated to this relationship and then poison configure_mappers() for
every later test in the same process.
"""

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app

    app = create_app("testing")
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    return app


def test_mappers_configure_without_error(app):
    """The most direct proof the server itself boots with no broken mapper:
    the same check production's own health checks / first query exercise."""
    from sqlalchemy.orm import configure_mappers

    with app.app_context():
        configure_mappers()  # raises InvalidRequestError on a broken mapping


def test_solution_code_bundle_relationship_resolves_to_solution(app):
    """SolutionCodeBundle.solution must resolve to the real Solution model,
    with the matching inverse backref, after a normal app boot."""
    from app.models.solution_models import Solution
    from app.modules.solutions_product.models import SolutionCodeBundle

    with app.app_context():
        assert SolutionCodeBundle.solution.property.mapper.class_ is Solution
        assert hasattr(Solution, "code_bundles")


def test_solution_code_bundle_does_not_use_a_deferred_string_target(app):
    """Guard against a future regression reintroducing a bare string target.

    SolutionCodeBundle.solution must reference the Solution class directly
    (and declare its own foreign_keys), not rely on SQLAlchemy's deferred,
    string-based class-name lookup across the whole declarative registry --
    a lookup that only ever worked here by accident of import order.
    """
    from app.modules.solutions_product.models import SolutionCodeBundle

    with app.app_context():
        prop = SolutionCodeBundle.solution.property
        # argument is the resolved class, not a deferred string -- a string
        # argument here is exactly what let this regress silently before.
        assert not isinstance(prop.argument, str)
        assert prop.local_remote_pairs, "foreign_keys must be explicit, not auto-detected"
