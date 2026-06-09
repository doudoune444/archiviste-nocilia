"""OIDC token provider seam for authenticated workers calls.

Responsibilities:
- Predicate: should the given workers URL receive an Authorization header?
- Audience derivation: normalise workers URL to scheme://host[:port].
- IdTokenProvider Protocol: seam injected into clients and runner.
- MetadataIdTokenProvider: production implementation via Google metadata server.
- OidcTokenError: raised when token fetch fails.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast, runtime_checkable
from urllib.parse import urlsplit, urlunsplit

from google.auth.transport.requests import Request
from google.oauth2.id_token import fetch_id_token
from pydantic import SecretStr

# Cast untyped google-auth function to a typed Callable so that call sites
# satisfy mypy --strict (no-untyped-call) without relaxing any strict flag.
# google.oauth2.id_token has no type stubs; the cast is the typed boundary.
_fetch_id_token: Callable[[Request, str], str] = cast(
    "Callable[[Request, str], str]", fetch_id_token
)

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def is_authenticated_target(workers_url: str) -> bool:
    """Return True iff the URL should receive an Authorization header.

    True when: scheme == https AND host ∉ {localhost, 127.0.0.1, ::1}.
    False for http (any host) or loopback hosts (any scheme).
    AC-1, AC-2, D-1.
    """
    parsed = urlsplit(workers_url)
    if parsed.scheme != "https":
        return False
    # urlsplit strips brackets from IPv6; hostname returns unbracketed form
    host = parsed.hostname or ""
    return host not in _LOOPBACK_HOSTS


def derive_audience(workers_url: str) -> str:
    """Return scheme://host[:port] — path/query/fragment stripped.

    Port is preserved when explicitly present; default port for scheme is omitted.
    IPv6 addresses are re-bracketed in the netloc (urlsplit strips brackets from
    .hostname but preserves them in .netloc when they were present).
    AC-4, D-2.
    """
    parsed = urlsplit(workers_url)
    # netloc already contains brackets for IPv6 and includes the port if present.
    # urlunsplit with empty path/query/fragment gives scheme://netloc.
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


class OidcTokenError(Exception):
    """Raised by IdTokenProvider.fetch when the token cannot be obtained."""


@runtime_checkable
class IdTokenProvider(Protocol):
    """Structural protocol: fetch a signed OIDC ID token for the given audience.

    Returns a SecretStr so the token bytes are never exposed in repr/str/logs.
    Raises OidcTokenError on failure.
    AC-5, D-4, D-6.
    """

    def fetch(self, audience: str) -> SecretStr:
        """Return a Google-signed ID token for *audience*."""
        ...  # pragma: no cover


class MetadataIdTokenProvider:
    """Production provider: fetches ID token from the Cloud Run metadata server.

    Uses google.oauth2.id_token.fetch_id_token(Request(), audience) which sources
    the token automatically from the GCP metadata server when running on Cloud Run
    (no GOOGLE_APPLICATION_CREDENTIALS required).
    Wraps the result in SecretStr to prevent accidental logging.
    AC-5, AC-9, OQ-3.
    """

    def fetch(self, audience: str) -> SecretStr:
        """Fetch and return a signed ID token for *audience* as SecretStr.

        Raises OidcTokenError if the metadata server is unreachable or returns an error.
        """
        try:
            token: str = _fetch_id_token(Request(), audience)
        except Exception as exc:
            raise OidcTokenError(f"failed to fetch OIDC token for {audience!r}") from exc
        return SecretStr(token)
