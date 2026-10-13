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
    import segno

    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(segno, "make_qr", _raise)

    with caplog.at_level(logging.DEBUG):
        result = qr_svg_data_uri(_URI)

    assert result == ""
    for record in caplog.records:
        assert _URI not in record.getMessage()
