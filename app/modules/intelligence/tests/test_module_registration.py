"""T-001 acceptance criteria 14 and 16: non-fatal registration.

Deliberately does NOT call ``create_app()`` a second time in this process:
``tests/test_boot_health.py`` documents that a second boot raises "can no
longer be called on the blueprint" for an unrelated blueprint
(``app/modules/codegen/services/sap_importer.py`` attaches routes after
registration), which would make this file's result depend on run order
rather than on the behaviour under test. Instead this exercises
``app._bootstrap.blueprints._register_intelligence`` directly against a
minimal stub app object — the same try/except path a real boot runs through,
without re-invoking ``create_app()``.

**Updated for T-003.** T-001's original ``test_register_mounts_no_blueprint_and_no_route``
asserted nothing was mounted, because T-001 shipped no query surface by
design (sdd-v2.md OA-5). T-003 is the task that adds one: the
``intelligence_api`` blueprint (recompute + provenance-expansion routes,
task 03), registered via the same non-fatal try/except this file already
exercised. The blueprint object itself constructs fine against a plain
Python stub (it needs no real Flask app until a request is dispatched), so
it mounts even here -- this file now asserts exactly ONE blueprint mounts,
named ``intelligence_api``, matching NFR-8 ("no query surface beyond these
two routes").

Mapping:
    14 -> test_registration_succeeds_when_module_is_importable,
          test_registration_is_non_fatal_when_import_is_forced_to_raise
    16 -> test_register_mounts_exactly_the_intelligence_api_blueprint
"""

from __future__ import annotations

import sys


class _StubLogger:
    def __init__(self):
        self.warnings = []
        self.infos = []

    def warning(self, msg, *args):
        self.warnings.append(msg % args if args else msg)

    def error(self, msg, *args):
        self.warnings.append(msg % args if args else msg)

    def info(self, msg, *args):
        self.infos.append(msg % args if args else msg)

    def exception(self, msg, *args):
        self.warnings.append(msg % args if args else msg)


class _StubApp:
    def __init__(self):
        self.blueprints = {}
        self.logger = _StubLogger()

    def register_blueprint(self, bp, **kwargs):
        self.blueprints[bp.name] = bp


def test_register_mounts_exactly_the_api_and_ui_blueprints():
    """The API blueprint carries every route under ``/api/v1/intelligence``; the
    only other blueprint is the UI one, which serves the two pages under
    ``/intelligence`` and no route under ``/api/``.

    T-004 added a third route (API-1, US-1 impact) to this SAME blueprint --
    see docs/buckets/t004-us1-impact-endpoint/tasks/00-verification-notes.md
    defect D1. T-005 adds a fourth (API-5, US-5 yield) -- see
    docs/buckets/t005-us5-yield-report/tasks/00-verification-notes.md
    section C -- so the route count below moved from 3 to 4, but the
    one-blueprint invariant this test exists to pin is unchanged.
    """
    from app.modules.intelligence import register

    stub = _StubApp()
    register(stub)
    assert set(stub.blueprints.keys()) == {"intelligence_api", "intelligence_ui"}

    bp = stub.blueprints["intelligence_api"]
    # Blueprint.deferred_functions holds the registration callables, not the
    # rules directly (rules only materialise once bound to a real app); count
    # them instead, which is stable without booting a real Flask app.
    assert len(bp.deferred_functions) == 4, (
        "exactly four routes: POST .../recompute, GET .../derived/<id>, "
        "GET .../impact/<element_id>, GET .../yield"
    )


def test_registration_succeeds_when_module_is_importable():
    sys.modules.pop("app.modules.intelligence", None)  # force a fresh import
    from app._bootstrap.blueprints import _register_intelligence

    stub = _StubApp()
    _register_intelligence(stub)

    assert set(stub.blueprints.keys()) == {"intelligence_api", "intelligence_ui"}
    assert any("intelligence" in msg.lower() for msg in stub.logger.infos)


def test_registration_is_non_fatal_when_import_is_forced_to_raise(monkeypatch):
    monkeypatch.setitem(sys.modules, "app.modules.intelligence", None)
    from app._bootstrap.blueprints import _register_intelligence

    stub = _StubApp()
    _register_intelligence(stub)  # must not raise

    assert stub.blueprints == {}
    assert any("intelligence" in msg.lower() for msg in stub.logger.warnings), (
        "a forced import failure must be logged as a warning, not swallowed silently"
    )
