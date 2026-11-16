"""The RSS reader must not expand entities from a feed it fetched from the internet."""
import pytest

from app.modules.organization.public_signals import sources

NORMAL = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Acme Corp opens a new office</title><link>https://example.test/a</link>
<description>Acme Corp grows</description><pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Unrelated news</title><link>https://example.test/b</link><description>Nothing</description></item>
</channel></rss>"""

EXTERNAL = """<?xml version="1.0"?>
<!DOCTYPE rss [<!ENTITY secret SYSTEM "file:///etc/hostname">]>
<rss version="2.0"><channel><item><title>Acme Corp &secret;</title>
<link>https://example.test/x</link><description>Acme Corp</description></item></channel></rss>"""

LAUGHS = """<?xml version="1.0"?>
<!DOCTYPE rss [
<!ENTITY a "ha"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;"><!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
<!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">
]>
<rss version="2.0"><channel><item><title>Acme Corp &d;</title>
<link>https://example.test/y</link><description>Acme Corp</description></item></channel></rss>"""


class FakeHttp:
    def __init__(self, body):
        self.body = body

    def text(self, url, **kw):
        return self.body


def run(body):
    return sources.rss(FakeHttp(body), "Acme Corp", {"rss_feeds": ["https://feed.test/rss"]})


def test_normal_feed_still_yields_its_matching_item():
    items = run(NORMAL)
    assert [i.title for i in items] == ["Acme Corp opens a new office"]
    assert items[0].url == "https://example.test/a"


@pytest.mark.parametrize("body", [EXTERNAL, LAUGHS], ids=["external-entity", "billion-laughs"])
def test_feed_declaring_entities_is_refused(body):
    with pytest.raises(ValueError):
        run(body)
