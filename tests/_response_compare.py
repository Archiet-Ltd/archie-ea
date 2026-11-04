"""Helpers for comparing two whole responses that differ only by the id the caller typed."""
import re


def _without_per_request_nonce(html_text):
    """Strip the CSP nonce every response carries — a fresh random value on
    each request, present regardless of which id was asked for — so two
    separate requests' bodies can be compared for anything else."""
    return re.sub(r'nonce="[^"]*"', 'nonce="NONCE"', html_text)


def _without_typed_id(html_text, path, typed_id):
    """Replace the id the caller typed wherever the page echoes it back: the
    not-found message ("#<id>") and the first-week two-factor reminder, whose
    "Set it up" link returns to the requested path. Both repeat the caller's
    own URL, so two requests that differ only in that id differ there by
    design and carry nothing the caller did not already have."""
    html_text = re.sub(rf"(?<!&)#{typed_id}(?![\da-fA-F])", "#ID", html_text)
    html_text = re.sub(rf"(?<=return={re.escape(path)}/){typed_id}(?!\d)", "ID", html_text)
    return _without_per_request_nonce(html_text)


def without_typed_id_everywhere(body, typed_id, path=""):
    """Replace the typed id where a whole response repeats it (a "#id" message or the
    requested path with the id), and strip per-request values (nonce, request id,
    timestamp, form token), so two whole bodies compare in full. A 32-character address
    is replaced anywhere; a short numeric id only in those two shapes, so an unrelated
    "8" or "10" elsewhere in the page is never touched."""
    typed = re.escape(str(typed_id))
    if re.fullmatch(r"[0-9a-f]{32}", str(typed_id)):
        body = re.sub(typed, "ID", body)
    else:
        body = re.sub(rf"(?<![&\w])#{typed}(?![\w])", "#ID", body)
        if path:
            body = re.sub(rf"(?<={re.escape(path)}/){typed}(?![\w])", "ID", body)
    body = _without_per_request_nonce(body)
    body = re.sub(r'("request_id":")[^"]*"', r'\1REQ"', body)
    body = re.sub(r'("timestamp":")[^"]*"', r'\1TIME"', body)
    return re.sub(r'(csrf[-_]?token"?[^>]{0,40}?(?:content|value)=")[^"]*"', r'\1TOKEN"', body, flags=re.I)
