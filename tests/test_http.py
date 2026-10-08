"""The one place the pipeline talks to the network, tested without a network.

Three behaviours here are load-bearing for the rest of the project, so they are
the ones worth pinning down:

  * redact_url, because NASS Quick Stats carries its API key in the query string
    and storage.py writes URLs into data/manifest.jsonl. A leak here commits a
    secret to git.
  * the retry policy, because a 404 from NOAA is a FACT -- it is how the nClimGrid
    client learns a month is not finalised yet and falls back to prelim -- while a
    500 is a hiccup. Retrying the first only delays the fallback.
  * parse_last_modified, because that header is the publication date of an
    nClimGrid file and the fetch date is not.

urlopen is monkeypatched throughout; nothing here opens a socket.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any

import pytest

from pipeline.http import (
    BACKOFF_SECONDS,
    REDACTED,
    USER_AGENT,
    FetchError,
    fetch,
    fetch_with_headers,
    parse_last_modified,
    redact_url,
)

# tests/ is not a package (no __init__.py), so pytest's default prepend import
# mode puts this directory on sys.path and conftest is importable by name.
from conftest import FakeResponse

NASS = ("https://quickstats.nass.usda.gov/api/api_GET/"
        "?key=SECRETKEYVALUE&commodity_desc=CORN&format=JSON")


# ------------------------------------------------------------------ redaction


def test_an_api_key_in_the_query_string_is_replaced() -> None:
    safe = redact_url(NASS)
    assert "SECRETKEYVALUE" not in safe
    assert f"key={REDACTED}" in safe
    # Everything that is not a secret survives, so the URL stays diagnosable.
    assert "commodity_desc=CORN" in safe and "format=JSON" in safe


@pytest.mark.parametrize("name", ["key", "api_key", "apikey", "token", "access_token", "password"])
def test_every_secret_looking_parameter_is_covered(name: str) -> None:
    assert "s3cret" not in redact_url(f"https://example.test/x?{name}=s3cret")


def test_matching_ignores_case_so_an_unadded_source_is_still_covered() -> None:
    assert "s3cret" not in redact_url("https://example.test/x?API_KEY=s3cret&Token=s3cret")


def test_a_url_with_no_secret_is_returned_unchanged() -> None:
    plain = "https://www.ncei.noaa.gov/data/nclimgrid-daily/access/averages/2012/tmax-201207-cty-scaled.csv"
    assert redact_url(plain) == plain
    assert redact_url("https://example.test/path?year=2012") == "https://example.test/path?year=2012"


def test_a_blank_secret_is_still_redacted_rather_than_dropped() -> None:
    # keep_blank_values, so `?key=` does not silently vanish from the URL.
    assert redact_url("https://example.test/x?key=&year=2012") == \
        f"https://example.test/x?key={REDACTED}&year=2012"


# ------------------------------------------------------------- last-modified


def test_last_modified_is_read_as_aware_utc() -> None:
    stamp = parse_last_modified({"last-modified": "Thu, 01 Sep 2022 14:33:46 GMT"})
    assert stamp is not None and stamp.tzinfo is not None
    assert stamp.date().isoformat() == "2022-09-01"
    assert (stamp.hour, stamp.minute, stamp.second) == (14, 33, 46)


def test_last_modified_is_found_whatever_the_header_case() -> None:
    assert parse_last_modified({"Last-Modified": "Sun, 06 Sep 2026 10:47:10 GMT"}) is not None


def test_a_missing_or_unparseable_last_modified_is_none_rather_than_a_guess() -> None:
    assert parse_last_modified({}) is None
    assert parse_last_modified({"last-modified": "yesterday afternoon"}) is None
    assert parse_last_modified({"last-modified": ""}) is None


# -------------------------------------------------------------------- fetching


def _serve(monkeypatch: pytest.MonkeyPatch, *answers: Any) -> list[urllib.request.Request]:
    """Queue answers for successive urlopen calls and record the requests made."""
    seen: list[urllib.request.Request] = []
    queue = list(answers)

    def fake(request: urllib.request.Request, timeout: int | None = None) -> FakeResponse:
        seen.append(request)
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return seen


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.test/x", code, "nope", {}, None)


def test_a_body_and_its_headers_come_back_with_keys_lower_cased(monkeypatch) -> None:
    _serve(monkeypatch, FakeResponse(b"payload", headers={"Last-Modified": "x", "ETag": "y"}))
    body, headers = fetch_with_headers("https://example.test/x")
    assert body == b"payload"
    # Lower-cased so a caller never has to guess which spelling a server used.
    assert headers == {"last-modified": "x", "etag": "y"}


def test_the_project_agent_is_sent_by_default_and_accept_only_when_asked(monkeypatch) -> None:
    seen = _serve(monkeypatch, FakeResponse(b"{}"), FakeResponse(b"{}"))
    fetch("https://example.test/x")
    assert seen[0].get_header("User-agent") == USER_AGENT
    assert seen[0].get_header("Accept") is None

    fetch("https://example.test/x", accept="application/json")
    assert seen[1].get_header("Accept") == "application/json"


def test_one_source_can_override_the_agent_without_changing_the_others(monkeypatch) -> None:
    seen = _serve(monkeypatch, FakeResponse(b"{}"))
    fetch("https://example.test/x", user_agent="Mozilla/5.0")
    assert seen[0].get_header("User-agent") == "Mozilla/5.0"


def test_a_404_is_not_retried_because_it_is_an_answer(monkeypatch) -> None:
    # nClimGrid relies on this: the scaled file for the current month 404s, and
    # that is how the client learns to ask for prelim instead. Three attempts
    # would only delay the fallback.
    seen = _serve(monkeypatch, _http_error(404))
    with pytest.raises(FetchError) as caught:
        fetch("https://example.test/x", retries=3)
    assert len(seen) == 1
    assert caught.value.status == 404


@pytest.mark.parametrize("code", [408, 425, 429, 500, 502, 503, 504])
def test_a_transient_status_is_retried_to_the_limit(monkeypatch, code: int) -> None:
    seen = _serve(monkeypatch, *[_http_error(code)] * 3)
    waits: list[float] = []
    with pytest.raises(FetchError) as caught:
        fetch_with_headers("https://example.test/x", retries=3, sleep=waits.append)
    assert len(seen) == 3
    assert caught.value.status == code
    # Exponential, and no wait after the final failure.
    assert waits == [BACKOFF_SECONDS, BACKOFF_SECONDS * 2]


def test_a_retry_that_succeeds_returns_the_body(monkeypatch) -> None:
    _serve(monkeypatch, _http_error(503), FakeResponse(b"second time"))
    assert fetch_with_headers("https://example.test/x", sleep=lambda _: None)[0] == b"second time"


def test_a_connection_failure_is_retried_and_then_reported(monkeypatch) -> None:
    _serve(monkeypatch, *[urllib.error.URLError("no route")] * 3)
    with pytest.raises(FetchError) as caught:
        fetch_with_headers("https://example.test/x", retries=3, sleep=lambda _: None)
    assert caught.value.status is None
    assert "URLError" in str(caught.value)


def test_an_error_never_carries_the_key_that_caused_it(monkeypatch) -> None:
    _serve(monkeypatch, _http_error(404))
    with pytest.raises(FetchError) as caught:
        fetch(NASS)
    # Both the message and the attribute, since either could reach a log.
    assert "SECRETKEYVALUE" not in str(caught.value)
    assert "SECRETKEYVALUE" not in caught.value.url
    assert REDACTED in caught.value.url
