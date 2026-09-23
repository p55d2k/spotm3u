"""Download failure categories and error redaction helpers.

Kept separate from :mod:`spotm3u.online.downloader` so the categories and the
redaction helpers can be shared by the downloader and by the YouTube setup
diagnostics without either importing the other.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# YouTube failure categories. Each maps to a different, actionable setup or
# retry instruction, so they are deliberately not collapsed into one result.
AUTHENTICATION_REQUIRED = "authentication_required"
BROWSER_COOKIE_ERROR = "browser_cookie_error"
PO_TOKEN_UNAVAILABLE = "po_token_unavailable"
CONTENT_UNAVAILABLE = "content_unavailable"
RATE_LIMITED = "rate_limited"
DOWNLOAD_FAILURE = "download_failure"


class DownloadError(RuntimeError):
    """Raised when a source cannot be downloaded into a complete MP3 file."""


def _redact_url(url: str | None) -> str | None:
    """Strip credentials embedded in a URL, keeping the rest intact."""
    if not url:
        return url
    parsed = urlparse(url)
    if parsed.username is None and parsed.password is None:
        return url
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return parsed._replace(netloc=host).geturl()


def _redact_error(message: str) -> str:
    """Remove cookies, tokens, credentials, and URL userinfo from a message."""
    if not message:
        return message
    redacted = re.sub(r"(?i)\b(https?://)[^\s/@]+@", r"\1", message)
    redacted = re.sub(r"(?i)\b(cookie\s*[:=]\s*)\S.*", r"\1[redacted]", redacted)
    redacted = re.sub(
        r"(?i)\b(authorization\s*[:=]\s*)(?:bearer\s+)?\S+", r"\1[redacted]", redacted
    )
    redacted = re.sub(
        r"(?i)\b(po_?token|pot_token|token|password|passwd|secret|api_?key)([=:]\s*)\S+",
        r"\1\2[redacted]",
        redacted,
    )
    return redacted
