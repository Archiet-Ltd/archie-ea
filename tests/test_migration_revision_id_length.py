"""alembic_version.version_num is varchar(32): a longer revision id makes the
migration's final version stamp fail with StringDataRightTruncation and aborts
the deploy after the migration body has already run."""

import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parent.parent / "migrations" / "versions"
_REVISION = re.compile(r"""^revision\s*(?::\s*str\s*)?=\s*['"]([^'"]+)['"]""", re.M)


def test_every_revision_id_fits_alembic_version_num():
    too_long = []
    for path in sorted(VERSIONS.glob("*.py")):
        match = _REVISION.search(path.read_text(encoding="utf-8"))
        if match and len(match.group(1)) > 32:
            too_long.append(f"{path.name}: {match.group(1)} ({len(match.group(1))})")
    assert not too_long, "revision ids over 32 characters: " + "; ".join(too_long)
