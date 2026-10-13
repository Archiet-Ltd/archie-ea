"""Unit tests for app.utils.qr.qr_svg_data_uri (the enrolment QR helper)."""

from __future__ import annotations

import logging

import pytest

from app.utils.qr import qr_svg_data_uri

_URI = "otpauth://totp/Archie%20EA:admin%40example.com?secret=JBSWY3DPEHPK3PXP&issuer=Archie%20EA"
_URI_2 = "otpauth://totp/Archie%20EA:other%40example.com?secret=NB2HI4DTHIXS6Z3B&issuer=Archie%20EA"


def test_returns_a_data_uri_with_no_html_unsafe_characters():
    result = qr_svg_data_uri(_URI)

    assert result.startswith("data:image/svg+xml")
    for char in ('"', "'", "<", ">", "&"):
        assert char not in result


def test_matches_segno_output_directly_and_differs_for_different_input():
    import segno

    expected = segno.make_qr(_URI, error="m").svg_data_uri(
        scale=6, border=4, dark="#000", light="#fff"
    )
    result = qr_svg_data_uri(_URI)

    assert result == expected

    result_2 = qr_svg_data_uri(_URI_2)
    assert result_2 != result

    symbol = segno.make_qr(_URI, error="m")
    assert symbol.is_micro is False


def test_empty_and_none_input_return_empty_string():
    assert qr_svg_data_uri("") == ""
    assert qr_svg_data_uri(None) == ""


def test_exception_from_segno_returns_empty_string_and_never_logs_the_input(
    monkeypatch, caplog
):
    """The stub raises an exception whose message IS the input, so this
    test only passes if the handler never lets that text reach a log in
    any form -- not the formatted message, not exc_info, not exc_text.
    A handler changed to ``logger.exception(...)`` or passed
    ``exc_info=True`` fills ``exc_text`` as soon as the record is
    formatted, which is exactly what the checks below catch."""
    import segno

    _SECRET = "JBSWY3DPEHPK3PXP"
    assert _SECRET in _URI

    def _raise(*args, **kwargs):
        raise RuntimeError(args[0])

    monkeypatch.setattr(segno, "make_qr", _raise)

    with caplog.at_level(logging.DEBUG):
        result = qr_svg_data_uri(_URI)

    assert result == ""

    qr_records = [r for r in caplog.records if r.name == "app.utils.qr"]
    assert len(qr_records) == 1, "expected exactly one record from app.utils.qr"
    record = qr_records[0]
    assert record.levelno == logging.WARNING

    assert record.exc_info is None
    assert record.exc_text is None or _URI not in record.exc_text
    assert record.exc_text is None or _SECRET not in record.exc_text

    formatted = logging.Formatter("%(message)s").format(record)
    for needle in (_URI, _SECRET):
        assert needle not in record.getMessage()
        assert needle not in formatted
        assert needle not in caplog.text
