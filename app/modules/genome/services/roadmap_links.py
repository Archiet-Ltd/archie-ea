"""Where every older roadmap address goes: the one roadmap, with its lens and filter."""
from __future__ import annotations

from flask import redirect, url_for


def roadmap_url(lens: str | None = None, **filters) -> str:
    """The roadmap page address for a lens and any programme/solution/application filter."""
    args = {k: v for k, v in filters.items() if v is not None}
    if lens and lens != "strategic":
        args["lens"] = lens
    return url_for("genome_roadmap.index", **args)


def redirect_to_roadmap(lens: str | None = None, **filters):
    """302 to the one roadmap, carrying the lens and filter the old page stood for."""
    return redirect(roadmap_url(lens, **filters), code=302)
