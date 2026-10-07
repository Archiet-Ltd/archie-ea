"""R1-B04 ratchet: nothing new may build a work package row outside the one writer.

``app/services/work_package_service.py`` is the only writer of
``unified_work_packages`` and ``app/commands/consolidate_work_packages.py`` the
only copy path from the retired stores. The constructor calls of the five
work package classes that remain elsewhere are listed below, per file, with
their current counts. The counts can only fall: a new file, or a higher count,
fails here. R1-B04 PR 3 repoints the listed sites and empties the list; until
then the session bridge (app/services/work_package_bridge.py) carries what
they write into the one store.

``UnifiedWorkPackage(`` outside the writer has an allow-list of zero.
Tests under ``app/**/tests/`` are not scanned.
"""

from __future__ import annotations

import ast
import os

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")

CLASSES = {
    "WorkPackage",
    "RoadmapWorkPackage",
    "ImplementationWorkPackage",
    "TechnologyRoadmapInitiative",
    "UnifiedWorkPackage",
}
WRITER_FILES = {
    "app/services/work_package_service.py",
    "app/commands/consolidate_work_packages.py",
}

# file -> constructor calls of the retired classes still allowed (PR 3 empties this).
ALLOWED_RETIRED_CONSTRUCTORS = {
    "app/main/routes_application_roadmap.py": 1,
    "app/modules/ai_chat/services/ai_data_interaction_service.py": 2,
    "app/modules/ai_chat/services/workbench_kernel.py": 3,
    "app/modules/architecture/routes/architecture_assistant_routes.py": 1,
    "app/modules/architecture/services/gap_archimate_service.py": 2,
    "app/modules/architecture/services/gap_resolution_service.py": 1,
    "app/modules/architecture/services/goal_service.py": 1,
    "app/modules/capabilities/routes/archimate_cap_routes.py": 1,
    "app/modules/capabilities/services/capability_roadmap_dashboard_service.py": 1,
    "app/modules/interface_register/services/work_package_service.py": 1,
    "app/modules/solutions_strategic/v2/routes/solution_design_routes.py": 1,
    "app/modules/solutions_strategic/v2/routes/solution_phase_routes.py": 1,
    "app/modules/solutions_strategic/v2/services/roadmap_sync.py": 4,
    "app/modules/solutions_strategic/v2/services/strategic_service.py": 1,
    "app/services/ea_workflow_engine.py": 1,
    "app/services/enterprise_workflow_orchestrator.py": 4,
    "app/services/jira_connector_service.py": 1,
    "app/services/roadmap_builder_service.py": 1,
    "app/services/roadmap_sync.py": 4,
    "app/services/solution_archimate_sync_service.py": 1,
}
ALLOWED_UNIFIED_CONSTRUCTORS = {}


def _is_tests_dir(rel):
    return "/tests/" in rel or rel.endswith("/tests") or os.path.basename(rel).startswith("test_")


def _constructor_counts(app_dir=APP, root=ROOT):
    retired, unified = {}, {}
    for folder, _dirs, files in os.walk(app_dir):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if rel in WRITER_FILES or _is_tests_dir(rel):
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), filename=rel)
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                called = func.id if isinstance(func, ast.Name) else (
                    func.attr if isinstance(func, ast.Attribute) else None)
                if called not in CLASSES:
                    continue
                bucket = unified if called == "UnifiedWorkPackage" else retired
                bucket[rel] = bucket.get(rel, 0) + 1
    return retired, unified


def _violations(retired, unified):
    problems = []
    for rel, count in sorted(retired.items()):
        allowed = ALLOWED_RETIRED_CONSTRUCTORS.get(rel)
        if allowed is None:
            problems.append("%s builds a work package row (%d call(s)); use "
                            "work_package_service" % (rel, count))
        elif count > allowed:
            problems.append("%s builds %d work package rows, allowed %d; use "
                            "work_package_service" % (rel, count, allowed))
    for rel, count in sorted(unified.items()):
        allowed = ALLOWED_UNIFIED_CONSTRUCTORS.get(rel, 0)
        if count > allowed:
            problems.append("%s constructs UnifiedWorkPackage %d time(s), allowed %d; "
                            "only work_package_service writes that table" % (rel, count, allowed))
    return problems


def test_no_new_work_package_writer_outside_the_one_writer():
    retired, unified = _constructor_counts()
    assert _violations(retired, unified) == []


def test_unified_work_package_is_constructed_only_by_the_writer():
    _retired, unified = _constructor_counts()
    assert unified == {}, unified


def test_the_allow_list_only_shrinks():
    """Every listed file still has a call; remove an entry when its writer is repointed."""
    retired, _unified = _constructor_counts()
    stale = {rel: n for rel, n in ALLOWED_RETIRED_CONSTRUCTORS.items()
             if retired.get(rel, 0) < n}
    assert not stale, "lower these counts: %s" % stale


@pytest.mark.parametrize("snippet,expected", [
    ("from app.models.unified_work_package import UnifiedWorkPackage\n"
     "row = UnifiedWorkPackage(name='x')\n", "unified"),
    ("from app.models.implementation_migration import WorkPackage\n"
     "row = WorkPackage(name='x')\n", "retired"),
])
def test_ratchet_fails_when_a_new_writer_is_added(tmp_path, snippet, expected):
    """A scratch file under app/ that builds a row turns the ratchet red."""
    app_dir = tmp_path / "app"
    (app_dir / "services").mkdir(parents=True)
    scratch = app_dir / "services" / "scratch_new_writer.py"
    scratch.write_text(snippet)

    retired, unified = _constructor_counts(str(app_dir), str(tmp_path))
    problems = _violations(retired, unified)
    assert len(problems) == 1, problems
    assert "app/services/scratch_new_writer.py" in problems[0]
    assert ("UnifiedWorkPackage" in problems[0]) == (expected == "unified")
