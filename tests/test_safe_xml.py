"""Untrusted XML must not be able to exhaust server memory.

Regression test for the finding on 2026-07-30: every ArchiMate import path
called xml.etree.ElementTree.fromstring directly on user-uploaded files (up to
10MB), with the linter warning explicitly silenced via `# noqa: S314`. Stdlib
ElementTree does not resolve external entities, so file disclosure was not
reachable, but entity expansion was - a few kilobytes can expand to gigabytes
during parsing, which on a 3.8GB production box is a denial of service.

These tests run against BOTH code paths, because the deployed container image
predates the defusedxml requirement and therefore takes the fallback.
"""

import pytest

from app.utils import safe_xml

BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<lolz>&lol3;</lolz>"""

XXE_FILE_READ = b"""<?xml version="1.0"?>
<!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
<foo>&xxe;</foo>"""

LEGITIMATE = b"""<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/">
  <elements><element identifier="id-1"><name>Customer</name></element></elements>
</model>"""


@pytest.fixture(params=["defusedxml", "fallback"])
def parser_path(request, monkeypatch):
    """Exercise both layers. Production currently runs the fallback."""
    if request.param == "fallback":
        monkeypatch.setattr(safe_xml, "_HAVE_DEFUSEDXML", False)
    elif not safe_xml._HAVE_DEFUSEDXML:
        pytest.skip("defusedxml not installed in this environment")
    return request.param


@pytest.mark.parametrize("payload", [BILLION_LAUGHS, XXE_FILE_READ], ids=["billion_laughs", "xxe"])
def test_hostile_documents_are_refused(parser_path, payload):
    with pytest.raises(Exception) as excinfo:
        safe_xml.fromstring(payload)
    # Never a plain ParseError: that would mean it tried to parse it.
    assert not isinstance(excinfo.value, safe_xml.ParseError) or parser_path == "defusedxml"


def test_legitimate_archimate_still_parses(parser_path):
    root = safe_xml.fromstring(LEGITIMATE)
    ns = "{http://www.opengroup.org/xsd/archimate/3.0/}"
    assert len(root.findall(".//%selement" % ns)) == 1


def test_malformed_xml_still_raises_parse_error(parser_path):
    # Callers catch ParseError to return "XML parse error" to the user; that
    # behaviour must survive the hardening.
    with pytest.raises(safe_xml.ParseError):
        safe_xml.fromstring(b"<model><unclosed></model>")


def test_upload_route_uses_the_safe_parser():
    """Guard against a future edit reintroducing the raw parser on the upload path.

    Every model-import entry point (the import screen, its preview and the
    composer's JSON endpoint) hands the uploaded XML to the one OEF import
    engine, so the guard reads the engine; the routes that used to parse it
    themselves must not start doing so again with the raw parser.
    """
    import io as _io

    engine = _io.open("app/services/archimate_import_service.py", encoding="utf-8").read()
    assert "safe_xml.fromstring(xml_content)" in engine
    assert "ET.fromstring(" not in engine

    routes = _io.open(
        "app/modules/architecture/routes/archimate_routes.py", encoding="utf-8"
    ).read()
    assert "ET.fromstring(raw)" not in routes
