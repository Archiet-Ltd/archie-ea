"""Server-side QR code rendering.

One function, ``qr_svg_data_uri``, turns a string into an inline SVG
``data:`` URI an ``<img>`` tag can use directly. No file is written, no
route serves the image, and nothing is cached: the caller decides what to
do with the returned string, and it is gone once that call returns.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def qr_svg_data_uri(data: str) -> str:
    """Render ``data`` as a QR code and return it as an SVG ``data:`` URI.

    Returns ``""`` for empty or non-string input, and ``""`` on any
    rendering failure. A failure is logged with only the exception's class
    name -- never the input or the output -- because the input can be a
    live credential (a TOTP provisioning URI).
    """
    if not isinstance(data, str) or not data:
        return ""

    try:
        import segno

        # error="m" (never segno.make / the default make_qr behaviour that
        # can pick a Micro QR for short input): a Micro QR is not readable
        # by authenticator apps, which expect an ordinary QR symbol.
        qr = segno.make_qr(data, error="m")
        return qr.svg_data_uri(scale=6, border=4, dark="#000", light="#fff")
    except Exception as exc:
        logger.warning("QR render failed: %s", type(exc).__name__)
        return ""
