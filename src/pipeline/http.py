"""The one place the pipeline talks to the network.

Standard library only, one polite user agent, and one retry policy, so that every
source is fetched the same way and a rate limit or a schema event is diagnosable
from a single module. Three decisions are worth knowing about before writing a
client against it:

  * `fetch_with_headers` exists because the response headers are data. NOAA's
    Last-Modified is the publication date of an nClimGrid file -- the fetch date
    is not -- and it belongs in the raw manifest next to the bytes.
  * 4xx is not retried (429 excepted). A 404 from NOAA is a fact, not a hiccup:
    it is how you learn the scaled file for a month does not exist yet and the
    client should fall back to prelim. Retrying it three times only delays that.
  * `redact_url` is applied to every URL that leaves this module in an error and
    is reused by storage.py before a URL is written to disk, because NASS Quick
    Stats carries its API key in the query string.

No caching or archiving happens here; that is storage.RawStore's job.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Mapping

USER_AGENT = "climate-futures-pricing/0.1 (academic research)"
BACKOFF_SECONDS = 5.0
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
REDACTED_QUERY_PARAMS = frozenset({"key", "api_key", "apikey", "token", "access_token", "password"})
REDACTED = "REDACTED"


class FetchError(Exception):
    """A request failed after every attempt, or returned a status we will not retry."""

    def __init__(self, message: str, *, url: str, status: int | None = None) -> None:
        super().__init__(message)
        self.url = redact_url(url)
        self.status = status


def redact_url(url: str) -> str:
    """Replace secret-looking query parameter values, leaving the URL readable.

    Called before a URL is put in an error message, logged, or written to the raw
    manifest. Matching is on the parameter name, case-insensitively, so it also
    covers a source that has not been added yet.
    """
    parts = urllib.parse.urlsplit(url)
    if not parts.query:
        return url
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    if not any(name.lower() in REDACTED_QUERY_PARAMS for name, _ in pairs):
        return url
    cleaned = [
        (name, REDACTED if name.lower() in REDACTED_QUERY_PARAMS else value)
        for name, value in pairs
    ]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(cleaned)))


def parse_last_modified(headers: Mapping[str, str]) -> datetime | None:
    """The response's Last-Modified as a timezone-aware UTC datetime, if present.

    This is the publication date of the copy that was served. It is not always
    the vintage of the data inside it: NOAA stamps 2017-vintage archive files
    with their 2022 republication date, which is why the nClimGrid client also
    stores the version sidecar.
    """
    raw = headers.get("last-modified") or headers.get("Last-Modified")
    if not raw:
        return None
    try:
        stamp = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    return stamp.astimezone(timezone.utc) if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def fetch(url: str, *, accept: str | None = None, timeout: int = 120, retries: int = 3,
          user_agent: str | None = None) -> bytes:
    """Fetch a URL and return its body."""
    body, _ = fetch_with_headers(
        url, accept=accept, timeout=timeout, retries=retries, user_agent=user_agent
    )
    return body


def fetch_with_headers(url: str, *, accept: str | None = None, timeout: int = 120,
                       retries: int = 3, user_agent: str | None = None,
                       sleep: Callable[[float], None] = time.sleep) -> tuple[bytes, dict[str, str]]:
    """Fetch a URL, returning its body and its response headers (keys lower-cased).

    `retries` is the total number of attempts, matching the three tries in
    analysis/initial_analysis.py. Waits grow exponentially between them, so a
    throttled source backs off instead of hammering. `user_agent` overrides the
    project agent for the one source that rejects it (Yahoo); `sleep` is
    injectable so a test can exercise the backoff without waiting.
    """
    headers = {"User-Agent": user_agent or USER_AGENT}
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    attempts = max(1, retries)

    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(), {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRY_STATUSES:
                raise FetchError(
                    f"HTTP {exc.code} {exc.reason} for {redact_url(url)}",
                    url=url, status=exc.code,
                ) from exc
            if attempt == attempts - 1:
                raise FetchError(
                    f"HTTP {exc.code} {exc.reason} for {redact_url(url)} "
                    f"after {attempts} attempts",
                    url=url, status=exc.code,
                ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == attempts - 1:
                raise FetchError(
                    f"{type(exc).__name__}: {exc} for {redact_url(url)} "
                    f"after {attempts} attempts",
                    url=url,
                ) from exc
        sleep(BACKOFF_SECONDS * 2 ** attempt)

    raise FetchError(f"no attempt was made for {redact_url(url)}", url=url)  # unreachable
