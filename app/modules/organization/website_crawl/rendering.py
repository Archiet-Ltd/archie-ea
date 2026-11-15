"""Whether a page needs a real browser: only when the static page misses its content.

Clear cases are decided here without a model. The unclear ones in between are put to the
page-rendering-strategy prompt, and its answer is read the way OperateIQ read it: a browser
when the answer is Chromium with confidence of 0.7 or more, or when it is under 0.5 (unsure
is treated as needing one).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Callable, Optional

from . import prompts
from .extraction import soup_of, visible_text

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.7
LOW_CONFIDENCE = 0.5
THIN_TEXT = 50
SHORT_TEXT = 200
FULL_TEXT = 800

_MOUNT_IDS = ("root", "app", "__next", "__nuxt", "react-root", "svelte")


def parse_json(text: str) -> dict:
    """The JSON object in a model answer, with or without a code fence around it."""
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.S)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the answer")
    value = json.loads(body[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("the answer is not a JSON object")
    return value


def _empty_mount(soup) -> bool:
    return any(
        node is not None and not node.get_text(strip=True)
        for node in (soup.find(id=name) for name in _MOUNT_IDS)
    )


def needs_browser(url: str, html: str, status: int, llm: Optional[Callable[[str], str]]) -> bool:
    soup = soup_of(html)
    scripts = [s for s in soup.find_all("script")
               if "json" not in (s.get("type") or "").lower() and (s.get("src") or (s.string or "").strip())]
    text_length = len(visible_text(html))
    if scripts and (_empty_mount(soup) or text_length < THIN_TEXT):
        return True
    if text_length >= FULL_TEXT or not scripts:
        return False
    sources = [s["src"] for s in scripts if s.get("src")]
    metas = ["%s: %s" % (m.get("name") or m.get("property"), m["content"])
             for m in soup.find_all("meta") if m.get("content") and (m.get("name") or m.get("property"))][:20]
    prompt = prompts.render("page_rendering_strategy", {
        "url": url, "title": soup.title.string.strip() if soup.title and soup.title.string else "",
        "status_code": status, "meta_tags": metas, "scripts": sources[:40], "initial_html_snippet": html[:4000],
    })
    try:
        answer = parse_json(llm(prompt))
        confidence = float(answer.get("confidence"))
        chromium = answer.get("rendering_strategy") == "chromium"
    except Exception:
        logger.info("rendering strategy unavailable for %s; deciding from the text alone", url, exc_info=True)
        return text_length < SHORT_TEXT
    if chromium and confidence >= CONFIDENCE_THRESHOLD:
        return True
    return confidence < LOW_CONFIDENCE
