"""The company step shows what a website states at once; what the model works out follows as its own job."""

from playwright.sync_api import expect


def wait_for_the_model_pass(page, label="Key person", seconds=150):
    """Reload the company step until the fact the model pass adds is on it (the bell tells a real person the same)."""
    row = page.locator("[data-fact]").filter(has=page.get_by_test_id("fact-label").get_by_text(label, exact=True))
    for _ in range(max(1, seconds // 4)):
        if row.count():
            break
        page.wait_for_timeout(4000)
        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_test_id("signup-facts")).to_be_visible()
    assert row.count(), "the model pass never added %r within %d seconds" % (label, seconds)
