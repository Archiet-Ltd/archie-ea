"""One way to send rows to the browser as a CSV download (R1-B88).

``csv_attachment`` streams the rows, names the file, and stops spreadsheet
formula injection: a text cell that starts with ``=``, ``+``, ``-`` or ``@``
(or a tab or carriage return, which spreadsheets also treat as a lead-in) is
prefixed with ``'`` so it opens as text, not as a formula. Numbers and dates are
written as they are.
"""

import csv
import io
import re
from typing import Iterable, Sequence

from flask import Response

_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")
_UNSAFE_FILENAME = re.compile(r'[^A-Za-z0-9._-]+')


def neutralise_cell(value):
    """Return ``value`` as a CSV cell that cannot be read as a formula."""
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith(_FORMULA_LEADS):
        return "'" + value
    return value


def csv_attachment(filename: str, header: Sequence, rows: Iterable[Sequence]) -> Response:
    """A streamed ``text/csv`` attachment of ``header`` then ``rows``."""
    safe_name = _UNSAFE_FILENAME.sub("_", filename or "export.csv").strip("._") or "export.csv"

    def generate():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([neutralise_cell(cell) for cell in header])
        yield buffer.getvalue()
        for row in rows:
            buffer.seek(0)
            buffer.truncate(0)
            writer.writerow([neutralise_cell(cell) for cell in row])
            yield buffer.getvalue()

    response = Response(generate(), mimetype="text/csv")
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = 'attachment; filename="%s"' % safe_name
    response.headers["Cache-Control"] = "no-store"
    return response
