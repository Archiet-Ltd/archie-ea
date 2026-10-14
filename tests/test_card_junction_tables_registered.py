"""The kanban card junction tables must exist in a schema built right after boot.

``_schema`` (tests/conftest.py) and ``flask init-db`` both call ``create_all()``
once, straight after the app is created. ``adm_kanban_junctions`` used to be
imported only lazily, so a schema built before that import never got
``card_applications`` / ``card_archimate_elements``, and any later ORM cascade
from a user or application (deleting a user, the security-genome seed reversal)
failed with "relation does not exist" -- but only in a run where an earlier test
had imported the module, which is why it looked like a test-order flake.
"""
import subprocess
import sys
import textwrap

JUNCTION_TABLES = (
    "card_applications",
    "card_archimate_elements",
    "card_capabilities",
    "card_dependencies",
    "card_initiatives",
    "card_systems",
)


def test_junction_tables_are_registered_by_app_boot_alone():
    # A fresh interpreter: in-process, another test may already have imported the module.
    probe = textwrap.dedent(
        f"""
        import sys
        from app import create_app, db
        create_app("testing")
        missing = [t for t in {JUNCTION_TABLES!r} if t not in db.metadata.tables]
        sys.exit("missing from metadata: " + ", ".join(missing) if missing else 0)
        """
    )
    result = subprocess.run(  # nosec B603 -- fixed argv, no user input
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=300
    )
    assert result.returncode == 0, result.stderr[-1500:]
