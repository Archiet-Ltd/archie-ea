"""R1-B88: the shared CSV attachment helper."""

import csv
import io

from flask import Flask


def _body(response):
    return response.get_data(as_text=True)


def test_csv_attachment_headers_and_rows():
    from app.utils.csv_response import csv_attachment

    app = Flask(__name__)
    with app.test_request_context("/"):
        resp = csv_attachment("report.csv", ["a", "b"], [[1, "x"], [2, "y"]])
        assert resp.headers["Content-Type"] == "text/csv; charset=utf-8"
        assert resp.headers["Content-Disposition"] == 'attachment; filename="report.csv"'
        rows = list(csv.reader(io.StringIO(_body(resp))))
    assert rows == [["a", "b"], ["1", "x"], ["2", "y"]]


def test_cells_that_could_be_formulas_are_neutralised():
    from app.utils.csv_response import csv_attachment

    app = Flask(__name__)
    with app.test_request_context("/"):
        resp = csv_attachment(
            "x.csv", ["=HEADER()"], [["=1+1", "+cmd", "-2+3", "@SUM(A1)", "\t=x", "plain", None, -5]]
        )
        rows = list(csv.reader(io.StringIO(_body(resp))))
    assert rows[0] == ["'=HEADER()"]
    assert rows[1] == ["'=1+1", "'+cmd", "'-2+3", "'@SUM(A1)", "'\t=x", "plain", "", "-5"]


def test_filename_cannot_break_out_of_the_header():
    from app.utils.csv_response import csv_attachment

    app = Flask(__name__)
    with app.test_request_context("/"):
        resp = csv_attachment('a"; filename="b\r\nX-Evil: 1.csv', ["h"], [])
    disposition = resp.headers["Content-Disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    assert disposition.count('"') == 2
    assert "X-Evil" not in resp.headers


def test_empty_filename_falls_back():
    from app.utils.csv_response import csv_attachment

    app = Flask(__name__)
    with app.test_request_context("/"):
        resp = csv_attachment("", ["h"], [])
    assert resp.headers["Content-Disposition"] == 'attachment; filename="export.csv"'
