"""A pool-of-one session does not pile up idle connections as test modules pass.

Behavioural proof for tests/conftest.py's autouse hygiene fixture. A nested pytest
session runs in pool mode (ARCHIE_TEST_POOL=one) over generated modules that each
build their own one-connection engine in a module-scoped fixture, run two queries
and leave the engine alone, as the many modules with a module-scoped ``create_app``
do. Each module records how many idle backends the database holds. Without the
hygiene fixture that count rises by one per module (and the server eventually
answers "too many clients"); with it the count stays flat.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = 12

MODULE_SOURCE = textwrap.dedent(
    '''
    import json
    import os

    import pytest
    from sqlalchemy import create_engine, text

    OUT = {out!r}


    @pytest.fixture(scope="module")
    def engine():
        # Like a module-scoped create_app: one engine, never disposed here.
        return create_engine(os.environ["TEST_DATABASE_URL"], pool_size=1, max_overflow=0)


    def test_module_{index}(engine):
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        with engine.connect() as connection:  # the second checkout reuses the connection
            idle = connection.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND state = 'idle' AND pid <> pg_backend_pid()"
                )
            ).scalar_one()
        with open(os.path.join(OUT, "{index:02d}.json"), "w") as handle:
            json.dump({{"module": {index}, "idle": idle}}, handle)
    '''
)


def test_idle_connections_do_not_grow_with_the_number_of_modules():
    out = Path(tempfile.mkdtemp(prefix="pool_nested_out_"))
    package = ROOT / "tests" / f"_nested_pool_{uuid.uuid4().hex[:8]}"
    package.mkdir()
    try:
        for index in range(MODULES):
            (package / f"test_nested_{index:02d}.py").write_text(
                MODULE_SOURCE.format(out=str(out), index=index), encoding="utf-8"
            )
        env = dict(os.environ, ARCHIE_TEST_POOL="one")
        env.pop("ARCHIE_TENANT_GUC_SELFCHECK", None)
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(package), "-q", "-p", "no:cacheprovider",
             "-o", "addopts=", "--rootdir", str(ROOT)],
            cwd=ROOT, env=env, text=True, capture_output=True, timeout=120,
        )
        recorded = sorted(
            (json.loads(path.read_text()) for path in out.glob("*.json")),
            key=lambda row: row["module"],
        )
        idle = [row["idle"] for row in recorded]
        output = result.stdout + result.stderr
        assert len(recorded) == MODULES, f"only {len(recorded)} modules ran:\n{output[-2000:]}"
        assert result.returncode == 0, output[-2000:]
        assert max(idle) - idle[0] <= 2, (
            f"idle backends grew with the number of modules: {idle} "
            f"(one left behind per module)"
        )
    finally:
        shutil.rmtree(package, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)
