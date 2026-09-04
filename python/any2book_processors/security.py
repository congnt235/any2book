from __future__ import annotations

import re

_SCRIPT_BLOCKS = re.compile(
    r"<(script|iframe|object|embed|form)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL
)
_SELF_CLOSING_DANGEROUS = re.compile(
    r"<(script|iframe|object|embed|form)\b[^>]*/?>", re.IGNORECASE | re.DOTALL
)
_EVENT_HANDLER = re.compile(r"\s+on[a-z]+\s*=\s*(['\"]).*?\1", re.IGNORECASE | re.DOTALL)
_JAVASCRIPT_URL = re.compile(
    r"\s+(href|src)\s*=\s*(['\"])\s*javascript:.*?\2", re.IGNORECASE | re.DOTALL
)
_REMOTE_IMAGE = re.compile(
    r"<img\b[^>]*\bsrc\s*=\s*(['\"])https?://.*?\1[^>]*>", re.IGNORECASE | re.DOTALL
)


def sanitize_html(value: str) -> tuple[str, list[str]]:
    """Remove active content and remote images before document conversion."""
    warnings: list[str] = []
    cleaned, count = _SCRIPT_BLOCKS.subn("", value)
    cleaned, count2 = _SELF_CLOSING_DANGEROUS.subn("", cleaned)
    if count + count2:
        warnings.append("Active HTML content was removed")
    cleaned, count = _EVENT_HANDLER.subn("", cleaned)
    cleaned, count2 = _JAVASCRIPT_URL.subn("", cleaned)
    if count + count2:
        warnings.append("Unsafe HTML attributes were removed")
    cleaned, count = _REMOTE_IMAGE.subn("", cleaned)
    if count:
        warnings.append("Remote images were omitted; provide local assets to embed them")
    return cleaned, warnings
