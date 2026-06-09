"""Unit + property tests for eval/oidc.py — AC-1, AC-2, AC-3, AC-4, AC-5, AC-9, AC-11."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import SecretStr

from eval.oidc import (
    IdTokenProvider,
    MetadataIdTokenProvider,
    OidcTokenError,
    derive_audience,
    is_authenticated_target,
)

# ---------------------------------------------------------------------------
# AC-1 / AC-2: is_authenticated_target truth table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # AC-1: https + remote host → True
        ("https://workers.example.run.app", True),
        ("https://workers.example.run.app/v1/retrieve", True),
        ("https://workers.example.run.app:8443", True),
        # AC-2: http scheme → False
        ("http://workers.example.run.app", False),
        # AC-2: localhost variants → False
        ("http://localhost:8000", False),
        ("https://localhost:8000", False),
        ("http://127.0.0.1:8000", False),
        ("https://127.0.0.1", False),
        # AC-2: IPv6 loopback → False
        ("http://[::1]:8000", False),
        ("https://[::1]:8000", False),
    ],
)
def test_is_authenticated_target(url: str, expected: bool) -> None:
    # AC-1/AC-2: scheme https + host ∉ {localhost,127.0.0.1,::1} → True; else False
    assert is_authenticated_target(url) == expected


# ---------------------------------------------------------------------------
# AC-4: derive_audience strips path/query/trailing slash, preserves port
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected_audience"),
    [
        ("https://workers.example.run.app", "https://workers.example.run.app"),
        ("https://workers.example.run.app/", "https://workers.example.run.app"),
        ("https://workers.example.run.app/v1/retrieve", "https://workers.example.run.app"),
        # Port preserved
        ("https://workers.example.run.app:8443/v1/retrieve", "https://workers.example.run.app:8443"),
        # localhost normalises too
        ("http://localhost:8000/v1/retrieve", "http://localhost:8000"),
        # IPv6 loopback — hostname must be re-bracketed (AC-4 edge case)
        ("https://[::1]:9000/v1/retrieve", "https://[::1]:9000"),
    ],
)
def test_derive_audience(url: str, expected_audience: str) -> None:
    # AC-4: audience = scheme://host[:port] — path/query/slash stripped; port preserved
    assert derive_audience(url) == expected_audience


# ---------------------------------------------------------------------------
# AC-4 property: derive_audience idempotent across path variants
# ---------------------------------------------------------------------------


_HOST_STRATEGY = st.from_regex(
    r"[a-z][a-z0-9-]{0,8}\.[a-z]{2,4}", fullmatch=True
)
_PATH_STRATEGY = st.one_of(
    st.just(""),
    st.just("/"),
    st.just("/v1/retrieve"),
    st.just("/v1/generate"),
    st.just("/some/deep/path"),
)
_PORT_STRATEGY = st.one_of(st.none(), st.integers(min_value=1024, max_value=65535))


@given(host=_HOST_STRATEGY, path=_PATH_STRATEGY, port=_PORT_STRATEGY)
@settings(max_examples=50)
def test_derive_audience_idempotent(host: str, path: str, port: int | None) -> None:
    # AC-4 property: derive_audience(base) == derive_audience(base + "/" ) == derive_audience(base + "/v1/retrieve")
    port_str = f":{port}" if port else ""
    base = f"https://{host}{port_str}"
    assert derive_audience(base) == derive_audience(base + "/")
    assert derive_audience(base) == derive_audience(base + path)


# ---------------------------------------------------------------------------
# AC-5: IdTokenProvider is a Protocol (structural), MetadataIdTokenProvider conforms
# ---------------------------------------------------------------------------


def test_metadata_provider_conforms_to_protocol() -> None:
    # AC-5: MetadataIdTokenProvider structurally satisfies IdTokenProvider
    provider = MetadataIdTokenProvider()
    assert callable(getattr(provider, "fetch", None))


# ---------------------------------------------------------------------------
# AC-11: SecretStr redacts token in repr/str
# ---------------------------------------------------------------------------


def test_secret_str_redacts_in_repr() -> None:
    # AC-11: token held as SecretStr — repr/str must not reveal the value
    token_value = "eyJ.FAKE.TOKEN"
    secret = SecretStr(token_value)
    assert token_value not in repr(secret)
    assert token_value not in str(secret)


# ---------------------------------------------------------------------------
# AC-5: OidcTokenError is an Exception subclass
# ---------------------------------------------------------------------------


def test_oidc_token_error_is_exception() -> None:
    # AC-5: OidcTokenError signals provider failure
    err = OidcTokenError("metadata server unreachable")
    assert isinstance(err, Exception)
    assert "metadata server unreachable" in str(err)
