"""HTTP transport, kept deliberately thin.

Every line in this module is code that cannot be exercised against the real
thing from a sandbox with no outbound access, so there is as little of it as
possible. Everything with actual logic — parsing, column mapping,
normalisation — lives in the connectors and is tested against fixtures.

That split is the point. A connector written as one function that fetches,
parses and stores can only be tested by talking to the source, which means in
practice it is not tested at all. Split here, the untestable part is a dozen
lines of `urlopen` and the testable part is everything that decides what the
data means.

What is *not* verified
----------------------
This module has never made a successful request to any of the sources the
regions declare. It is written against their published documentation. The
first real run is therefore also the first test, and the connectors are built
to fail loudly and legibly at that moment rather than to guess — see
`sentinel/ingest/dma_ais.py` for how a schema mismatch is reported.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

__all__ = ["FetchError", "Fetcher", "UrllibFetcher"]

_USER_AGENT = "sentinel/2.0 (OSINT warning platform; contact via repository)"


class FetchError(RuntimeError):
    """A source could not be reached or refused the request.

    Distinct from a parse failure on purpose: one means "come back later",
    the other means "the format changed". Collapsing them would let a broken
    schema masquerade as an outage for as long as nobody read the logs.
    """


class Fetcher(Protocol):
    """Anything that can turn a URL into bytes.

    Injected everywhere it is used, so a connector's whole path — request
    through to stored rows — can be exercised with a fake in a test.
    """

    def get(self, url: str, timeout: float = 60.0) -> bytes:
        ...


@dataclass(frozen=True)
class UrllibFetcher:
    """The real one. No dependency beyond the standard library.

    `httpx` and `requests` are both already in the tree, but neither adds
    anything here: there is no session to reuse across daily archive files and
    no auth to negotiate for the open sources. One fewer import in the path
    that runs unattended is worth more than the ergonomics.
    """

    retries: int = 3
    backoff_seconds: float = 2.0
    user_agent: str = _USER_AGENT

    def get(self, url: str, timeout: float = 60.0) -> bytes:
        last: Exception | None = None
        for attempt in range(max(self.retries, 1)):
            request = urllib.request.Request(
                url, headers={"User-Agent": self.user_agent})
            try:
                with urllib.request.urlopen(request, timeout=timeout) as reply:
                    return reply.read()
            except urllib.error.HTTPError as exc:
                # 4xx will not fix itself by waiting; 5xx might.
                if 400 <= exc.code < 500:
                    raise FetchError(
                        f"{url} returned {exc.code} {exc.reason}") from exc
                last = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc

            if attempt + 1 < max(self.retries, 1):
                time.sleep(self.backoff_seconds * (2 ** attempt))

        raise FetchError(f"{url} unreachable after {self.retries} attempts: "
                         f"{type(last).__name__}: {last}")
