"""The one axe helper and the one real-dark check the smoke journeys share.

The audit module (test_accessibility_audit) owns the tag set; this module runs axe with it, so the
guard test_every_axe_run_in_tests_smoke_uses_the_audit_tag_set reads one tag list, imported from
the audit. Dark mode is chosen the way the product chooses it: partials/_head.html adds `dark` to
<html> when no theme is stored and the browser prefers a dark colour scheme, so a journey asks for
a dark browser context (color_scheme="dark"). Setting the class from an init script runs before
<html> exists and throws, which leaves the page light.
"""

from .test_accessibility_audit import TAGS


def _axe_violations(page, rules=None, impacts=None):
    """axe violations on `page` under the audit's tag set.

    With `rules`, only those rule ids; with `impacts`, only those impacts; with both, a violation
    that matches either. With neither, every violation.
    """
    from axe_playwright_python.sync_playwright import Axe

    report = Axe().run(page, options={"runOnly": {"type": "tag", "values": TAGS}})
    data = report.response if hasattr(report, "response") else report
    violations = data.get("violations", [])
    if rules is None and impacts is None:
        return violations
    return [
        v for v in violations
        if (rules is not None and v["id"] in rules)
        or (impacts is not None and v.get("impact") in impacts)
    ]


def new_context(browser, width, height, dark):
    """A browser context in the product's own light or dark theme."""
    return browser.new_context(
        viewport={"width": width, "height": height},
        color_scheme="dark" if dark else "light",
    )


def assert_theme(page, dark, where=""):
    """The page really renders in the theme the case claims: `dark` class and background token."""
    state = page.evaluate(
        """() => {
            const probe = document.createElement('div');
            probe.style.backgroundColor = 'hsl(var(--background))';
            document.body.appendChild(probe);
            const token = getComputedStyle(probe).backgroundColor;
            probe.remove();
            return {
                dark: document.documentElement.classList.contains('dark'),
                token: token,
                body: getComputedStyle(document.body).backgroundColor,
                raw: getComputedStyle(document.documentElement).getPropertyValue('--background').trim(),
            };
        }"""
    )
    label = "dark" if dark else "light"
    assert state["dark"] is dark, (
        "%s: expected the %s theme but <html> %s the dark class" % (
            where, label, "has" if state["dark"] else "does not have"))
    assert state["raw"] != "" and state["body"] == state["token"], (
        "%s: the page background %s is not the %s token's value %s" % (
            where, state["body"], label, state["token"]))
    return state
