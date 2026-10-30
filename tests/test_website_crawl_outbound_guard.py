"""The website reader fetches only public addresses: every request and every redirect hop is checked first."""
from __future__ import annotations

import socket
from types import SimpleNamespace

import pytest

from app.modules.organization.website_crawl import fetcher as fetcher_module
from app.modules.organization.website_crawl.fetcher import FetchRefused, Fetcher

PLAIN = "We could not read that address. Check it is your public website."
PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def production_rules(app):
    """Internal targets are refused, as in production."""
    previous = app.config.get("ALLOW_INTERNAL_WEBHOOK_TARGETS")
    app.config["ALLOW_INTERNAL_WEBHOOK_TARGETS"] = False
    with app.app_context():
        yield
    app.config["ALLOW_INTERNAL_WEBHOOK_TARGETS"] = previous


@pytest.fixture
def sockets(monkeypatch):
    """Every attempt to open a connection is recorded and refused."""
    opened = []

    def connect(self, address, *args, **kwargs):
        opened.append(address)
        raise OSError("no sockets in this test")

    def create_connection(address, *args, **kwargs):
        opened.append(address)
        raise OSError("no sockets in this test")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    return opened


def _resolving(monkeypatch, table):
    real = socket.getaddrinfo

    def getaddrinfo(host, port, *args, **kwargs):
        if host in table:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table[host], port or 80))]
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


class _Response:
    def __init__(self, status=200, headers=None, body=b"<html><body>hello</body></html>", peer=None):
        self.status_code = status
        self.headers = {"Content-Type": "text/html", **(headers or {})}
        self.encoding = "utf-8"
        sock = SimpleNamespace(getpeername=lambda: (peer, 443)) if peer else None
        self.raw = SimpleNamespace(read=lambda n, decode_content=True: body, _connection=SimpleNamespace(sock=sock))

    def close(self):
        pass


@pytest.mark.parametrize("address", [
    "http://127.0.0.1/", "http://10.0.0.1/", "http://172.16.0.1/", "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data/", "http://[::1]/",
])
def test_a_non_public_address_is_refused_before_any_socket_opens(sockets, address):
    reader = Fetcher(address)
    with pytest.raises(FetchRefused) as refused:
        reader.get(address + "robots.txt" if address.endswith("/") else address)
    assert str(refused.value) == PLAIN
    assert sockets == []


def test_a_name_that_resolves_to_loopback_is_refused_before_any_socket_opens(sockets, monkeypatch):
    _resolving(monkeypatch, {"rebind.example": "127.0.0.1"})
    with pytest.raises(FetchRefused) as refused:
        Fetcher("https://rebind.example").get("https://rebind.example/")
    assert str(refused.value) == PLAIN
    assert sockets == []


def test_a_redirect_to_the_metadata_address_stops_the_read(sockets, monkeypatch):
    _resolving(monkeypatch, {"www.example.com": PUBLIC_IP})
    asked = []

    def get(url, **kwargs):
        asked.append(url)
        return _Response(302, {"Location": "http://169.254.169.254/latest/meta-data/"})

    monkeypatch.setattr(fetcher_module.requests, "get", get)
    with pytest.raises(FetchRefused) as refused:
        Fetcher("https://www.example.com").get("https://www.example.com/")
    assert str(refused.value) == PLAIN
    assert asked == ["https://www.example.com/"]
    assert sockets == []


def test_robots_txt_is_checked_like_any_other_request(sockets):
    reader = Fetcher("http://169.254.169.254")
    reader.load_robots()
    assert reader.allowed("http://169.254.169.254/") is False
    assert sockets == []


def test_a_connection_that_lands_on_a_private_address_is_refused(monkeypatch):
    """The name looked public when checked and the connection went to loopback (DNS rebinding)."""
    _resolving(monkeypatch, {"www.example.com": PUBLIC_IP})
    monkeypatch.setattr(fetcher_module.requests, "get", lambda url, **kw: _Response(peer="127.0.0.1"))
    with pytest.raises(FetchRefused) as refused:
        Fetcher("https://www.example.com").get("https://www.example.com/")
    assert str(refused.value) == PLAIN


def test_a_public_site_is_still_read(monkeypatch):
    _resolving(monkeypatch, {"www.example.com": PUBLIC_IP})
    monkeypatch.setattr(fetcher_module.requests, "get", lambda url, **kw: _Response(peer=PUBLIC_IP))
    page = Fetcher("https://www.example.com").get("https://www.example.com/")
    assert page.status == 200 and "hello" in page.text
