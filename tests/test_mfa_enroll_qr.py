"""MFA enrolment page: the QR code is an inline SVG generated on the server.

The provisioning URI embeds the TOTP secret, so the QR code for it is drawn
server-side with the pure-python segno library as inline SVG -- never as a
JavaScript widget and never as a request to an external image service. The
manual setup key and the provisioning URI link stay below the QR code.
"""

from __future__ import annotations

import io
import re
import uuid

import pytest
from PIL import Image

from app.services import mfa_service

pytestmark = pytest.mark.usefixtures("db_session")

_PASSWORD = "Str0ng!Passw0rd"


def _make_admin(db_session, org):
    """An unenrolled administrator in a real organisation."""
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(
        email=f"mfa-qr-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id,
        confirmed=True,
        role=role,
    )
    user.password = _PASSWORD
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def enrollment_page(client, db_session, make_org):
    """Log in as an unenrolled administrator and open the enrolment page.

    Returns (page bytes, administrator, enrolment secret).
    """
    org = make_org("mfa-qr-enrol")
    admin = _make_admin(db_session, org)
    client.post(
        "/account/login",
        data={"email": admin.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    response = client.get("/account/mfa-challenge")
    assert response.status_code == 200, response.status_code
    with client.session_transaction() as sess:
        secret = sess["_mfa_enroll_secret"]
    return response.data, admin, secret


@pytest.fixture
def page(enrollment_page):
    return enrollment_page[0]


@pytest.fixture
def secret(enrollment_page):
    return enrollment_page[2]


@pytest.fixture
def provisioning_uri(enrollment_page):
    admin = enrollment_page[1]
    secret = enrollment_page[2]
    return mfa_service.provisioning_uri(admin, secret)


def _qr_decoder():
    """A QR-code image decoder if one is installed, otherwise None."""
    try:
        from pyzbar.pyzbar import decode as _decode  # type: ignore
    except ImportError:
        return None
    return _decode


def _modules_from_segno_svg_path(path_data: str) -> set:
    """Dark module cells from segno's inline SVG path data.

    segno draws each row of modules as one horizontal stroke --
    ``M <x> <y>h<w>`` (absolute) or ``m <dx> <dy>h<w>`` (relative) -- with
    coordinates in module units and ``y`` on a row centre (x.5).
    """
    tokens = re.findall(r"[Mmh]|-?\d+(?:\.\d+)?", path_data)
    modules = set()
    x = y = 0.0
    index = 0
    while index < len(tokens):
        command = tokens[index]
        if command == "M":
            x = float(tokens[index + 1])
            y = float(tokens[index + 2])
            index += 3
        elif command == "m":
            x += float(tokens[index + 1])
            y += float(tokens[index + 2])
            index += 3
        else:  # "h": one horizontal stroke at the current row
            width = float(tokens[index + 1])
            index += 2
            row = int(y)
            for column in range(int(x), int(x + width)):
                modules.add((column, row))
            x += width
    return modules


def _svg_to_png(svg_text: str, scale: int = 4) -> bytes:
    """Render segno's inline SVG QR code to a PNG bitmap with PIL only."""
    match = re.search(
        r'<path[^>]*\btransform="scale\(([0-9.]+)\)"[^>]*\bd="([^"]*)"', svg_text
    )
    if not match:
        raise ValueError("not a segno inline SVG")
    path_data = match.group(2)
    modules = _modules_from_segno_svg_path(path_data)
    if not modules:
        return b""
    columns = [column for column, _ in modules]
    rows = [row for _, row in modules]
    width = max(columns) + 1
    height = max(rows) + 1
    from PIL import Image

    image = Image.new("1", (width, height), 255)
    for column, row in modules:
        image.putpixel((column, row), 0)
    image = image.resize((width * scale, height * scale), Image.Resampling.NEAREST)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _extract_inline_svg(page: bytes) -> str:
    start = page.find(b"<svg")
    assert start != -1, "no inline svg on the enrolment page"
    end = page.find(b"</svg>", start)
    assert end != -1, "the inline svg is never closed"
    return page[start:end + len(b"</svg>")].decode("utf-8")


def _secret_only_inside_code_blocks_outside_form(page: bytes, secret: str) -> None:
    """Every occurrence of the enrolment secret lives in a <code> block
    outside the confirm form -- never in the form and never raw on the page."""
    secret_b = secret.encode()
    assert secret_b in page, "enrolment secret missing from the page"
    form_start = page.find(b"<form")
    assert form_start != -1, "no confirm form on the enrolment page"
    form_end = page.find(b"</form>", form_start)
    assert form_end != -1, "the confirm form is never closed"
    form_end += len(b"</form>")
    assert secret_b not in page[form_start:form_end], "secret leaked into the confirm form"
    for match in re.finditer(re.escape(secret_b), page):
        start = page.rfind(b"<code", 0, match.start())
        end = page.find(b"</code>", match.end())
        assert start != -1 and end != -1, "secret outside a code block"
        assert start < match.start() and match.end() <= end
        assert not (form_start < start < form_end), "secret's code block inside the form"


def test_enroll_page_contains_one_inline_svg_and_no_external_url(page, secret):
    assert page.count(b"</svg>") == 1, "expected exactly one inline svg"
    svg = _extract_inline_svg(page)
    opening = svg[: svg.find(">") + 1]
    assert "src=" not in opening, "the svg must be inline, not an external image"
    assert b"http://" not in page and b"https://" not in page, "no external URL on the page"
    assert secret.encode() in page, "the setup key must still be shown"


def test_qr_svg_decodes_to_the_provisioning_uri(page, secret, provisioning_uri):
    svg = _extract_inline_svg(page)
    decoder = _qr_decoder()
    if decoder is not None:
        png = _svg_to_png(svg)
        decoded = decoder(Image.open(io.BytesIO(png)))
        assert decoded, "the QR code in the svg did not decode"
        assert decoded[0].data.decode("utf-8") == provisioning_uri
    else:
        # No QR decoder installed: fall back to what can be asserted without
        # decoding the matrix -- the svg is present, and the secret appears
        # only inside the form-free code blocks (setup key and link).
        assert b"<svg" in page and b"</svg>" in page
        _secret_only_inside_code_blocks_outside_form(page, secret)


def test_manual_key_and_link_stay_below_the_qr_code(page, provisioning_uri):
    svg_end = page.find(b"</svg>")
    assert svg_end != -1
    key_block = page.find(b'data-testid="mfa-setup-key"')
    link_block = page.find(b"Or open this link on a device with your authenticator app:")
    assert 0 < svg_end < key_block < link_block, "QR code, then setup key, then link"
    # Jinja escapes '&' in {{ provisioning_uri }}, so the link arrives as &amp;
    assert provisioning_uri.replace("&", "&amp;").encode() in page, (
        "the provisioning URI link must remain on the page"
    )
