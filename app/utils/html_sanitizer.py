"""
Whitelist-based HTML sanitizer for user-generated rich text content.

Strips dangerous elements (script, iframe, etc.) and attributes (on* events,
javascript: URLs) while preserving safe formatting HTML from rich text
editors.

Usage as Jinja2 filter:
    {{ user_html_content | sanitize_html }}

Usage in Python:
    from app.utils.html_sanitizer import sanitize_html
    safe = sanitize_html(untrusted_html)
"""

import re
from html.parser import HTMLParser

from markupsafe import Markup

# Tags whose content (including children) is stripped entirely
_STRIP_CONTENT_TAGS = frozenset({"script", "style", "iframe", "object", "embed",
                                  "form", "input", "textarea", "select", "button",
                                  "link", "meta", "base", "applet"})

# Tags allowed in output (safe formatting tags)
SAFE_TAGS = frozenset({
    "a", "abbr", "b", "blockquote", "br", "caption", "cite", "code", "col",
    "colgroup", "dd", "div", "dl", "dt", "em", "figcaption", "figure",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "li", "mark",
    "ol", "p", "pre", "q", "s", "small", "span", "strong", "sub", "sup",
    "table", "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul",
})

# Allowed attributes per tag (* = all safe tags)
SAFE_ATTRS = {
    "*": frozenset({"class", "id", "title", "lang", "dir", "role",
                     "aria-label", "aria-hidden", "aria-describedby",
                     "data-lucide"}),
    "a": frozenset({"href", "rel", "target"}),
    "img": frozenset({"src", "alt", "width", "height", "loading"}),
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan", "scope"}),
    "ol": frozenset({"start", "type"}),
    "col": frozenset({"span"}),
    "colgroup": frozenset({"span"}),
}

_DANGEROUS_URL_RE = re.compile(r"^\s*(javascript|vbscript|data)\s*:", re.IGNORECASE)


def _escape_attr_value(value):
    """Escape an HTML attribute value (double-quote context)."""
    return (str(value)
            .replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def _escape_text(text):
    """Escape text content for safe HTML output."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


class _HTMLSanitizer(HTMLParser):
    """Whitelist-based HTML sanitizer using Python's standard HTMLParser."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self._parts = []
        self._strip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _STRIP_CONTENT_TAGS:
            self._strip_depth += 1
            return
        if self._strip_depth > 0:
            return
        if tag not in SAFE_TAGS:
            return
        safe_attrs = self._filter_attrs(tag, attrs)
        self._parts.append(self._format_open_tag(tag, safe_attrs))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _STRIP_CONTENT_TAGS:
            if self._strip_depth > 0:
                self._strip_depth -= 1
            return
        if self._strip_depth > 0:
            return
        if tag in SAFE_TAGS:
            self._parts.append(f"</{tag}>")

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self._strip_depth > 0 or tag not in SAFE_TAGS:
            return
        safe_attrs = self._filter_attrs(tag, attrs)
        self._parts.append(self._format_open_tag(tag, safe_attrs, self_closing=True))

    def handle_data(self, data):
        if self._strip_depth > 0:
            return
        self._parts.append(_escape_text(data))

    def handle_entityref(self, name):
        if self._strip_depth > 0:
            return
        self._parts.append(f"&{name};")

    def handle_charref(self, name):
        if self._strip_depth > 0:
            return
        self._parts.append(f"&#{name};")

    def _filter_attrs(self, tag, attrs):
        global_allowed = SAFE_ATTRS.get("*", frozenset())
        tag_allowed = SAFE_ATTRS.get(tag, frozenset())
        allowed = global_allowed | tag_allowed

        result = []
        for name, value in attrs:
            name = name.lower()
            if name.startswith("on"):
                continue
            if name not in allowed:
                continue
            if name in ("href", "src", "action") and value:
                if _DANGEROUS_URL_RE.match(value):
                    continue
            result.append((name, value))
        return result

    @staticmethod
    def _format_open_tag(tag, attrs, self_closing=False):
        if attrs:
            attr_str = " ".join(
                f'{n}="{_escape_attr_value(v)}"' if v is not None else n  # raw-html-ok: value is escaped via _escape_attr_value() inline above
                for n, v in attrs
            )
            suffix = " />" if self_closing else ">"
            return f"<{tag} {attr_str}{suffix}"
        return f"<{tag} />" if self_closing else f"<{tag}>"

    def get_output(self):
        return Markup("".join(self._parts))


def sanitize_html(html_string):
    """Sanitize HTML using a whitelist of safe tags and attributes.

    Strips:
    - <script>, <style>, <iframe>, <object>, <embed>, <form>, <input> (and content)
    - All on* event handler attributes (onclick, onerror, etc.)
    - javascript:, vbscript:, data: URL schemes in href/src attributes
    - Any tag not in the SAFE_TAGS whitelist (content is preserved, tag is dropped)

    Preserves:
    - Safe formatting tags (p, h1-h6, strong, em, ul, ol, li, a, img, table, etc.)
    - Safe attributes (class, id, href, src, alt, etc.)
    - Text content and HTML entities

    Returns a markupsafe.Markup instance safe for direct template rendering.
    """
    if not html_string:
        return Markup("")

    sanitizer = _HTMLSanitizer()
    sanitizer.feed(str(html_string))
    return sanitizer.get_output()
