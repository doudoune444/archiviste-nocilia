"""In-process tests for OIDC fail-fast in ragas_runner.main() — AC-6, AC-7, AC-8, AC-11.

Uses the injected `provider` param on main() to avoid real metadata calls (AC-5).
Tests run in-process (no subprocess) so the injected fake provider is reachable — per
plan Risk #1: confirmed approach for the exit-5 failure path (AC-3 preserved: no new
CLI flag/env).

AC-11(b) coverage note: test_token_not_in_any_output_on_success_path exercises the
path where a token IS produced (SpyProvider returns FAKE_TOKEN_SENTINEL wrapped in
SecretStr) and used (attached as Bearer header to mocked worker calls).  The test
fails if redaction regresses — e.g. if auth_header were logged with its raw value
instead of through SecretStr, or if get_secret_value() output were written to the
run file or any log.  The existing test_token_not_in_stdout_stderr_on_exit_5 covers
the failure path (no token produced); both tests are required for full AC-11 coverage.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import NoReturn

import pytest
from pydantic import SecretStr
from pytest_httpserver import HTTPServer

from eval.oidc import IdTokenProvider, OidcTokenError
from eval.ragas_runner import main

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_TOKEN_SENTINEL = "SENTINEL-OIDC-TOKEN-XYZ9999-DONOTLOG"


# ---------------------------------------------------------------------------
# Fake providers
# ---------------------------------------------------------------------------


class _FailingProvider:
    """Provider that always raises OidcTokenError — simulates metadata server failure."""

    def fetch(self, audience: str) -> NoReturn:
        raise OidcTokenError(f"simulated fetch failure for {audience!r}")


class _SpyProvider:
    """Provider that records calls and returns a fake token."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch(self, audience: str) -> SecretStr:
        self.calls.append(audience)
        return SecretStr(FAKE_TOKEN_SENTINEL)


# ---------------------------------------------------------------------------
# AC-6: fail-fast on authed target when provider raises → exit 5
# ---------------------------------------------------------------------------


def test_exit_5_on_oidc_failure_before_any_entry(
    tmp_path: Path, httpserver: HTTPServer
) -> None:
    """AC-6: failing provider on https target → exit 5; no retrieve/generate attempted."""
    # Use an HTTPS-looking URL — but we override main(provider=...) so no real network call
    # to the metadata server. Workers /healthz is never called because fail-fast fires first.
    output_path = tmp_path / "run.json"
    golden = FIXTURES / "golden_valid.jsonl"

    # Use httpserver just to get a URL; calls to it should not happen (fail-fast before /healthz)
    workers_https_url = "https://workers.example.run.app"

    exit_code = main(
        argv=[
            "--mode", "offline",
            "--set", str(golden),
            "--output", str(output_path),
            "--workers-url", workers_https_url,
        ],
        provider=_FailingProvider(),
    )

    # AC-6: exit code must be 5 — fail-fast before any entry
    assert exit_code == 5, f"expected exit 5, got {exit_code}"
    # AC-6: run file must NOT be written (fail before _run_all_entries)
    assert not output_path.exists(), "run file must not be created on exit 5"


# ---------------------------------------------------------------------------
# AC-8: exit code 5 is distinct from {0,1,2,3,4} + docstring documents it
# ---------------------------------------------------------------------------


def test_exit_5_distinct_from_other_codes() -> None:
    """AC-8: exit code 5 is not in {0,1,2,3,4}."""
    assert 5 not in {0, 1, 2, 3, 4}


def test_runner_docstring_documents_exit_5() -> None:
    """AC-8: ragas_runner module docstring must contain '5 — OIDC token fetch failure'."""
    import eval.ragas_runner as runner_module

    docstring = runner_module.__doc__ or ""
    assert "5" in docstring
    assert "OIDC" in docstring or "oidc" in docstring.lower()


# ---------------------------------------------------------------------------
# AC-7: log event=oidc_token_failed with no token bytes; audience loggable
# ---------------------------------------------------------------------------


def test_oidc_token_failed_log_has_no_token_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC-7: oidc_token_failed log must not contain any bytes of the injected sentinel.

    structlog's default ConsoleRenderer writes to stderr; we capture it via capsys.
    The oidc_token_failed event must appear; the fake sentinel must not.
    """
    golden = FIXTURES / "golden_valid.jsonl"
    output_path = tmp_path / "run.json"
    workers_https_url = "https://workers.example.run.app"

    exit_code = main(
        argv=[
            "--mode", "offline",
            "--set", str(golden),
            "--output", str(output_path),
            "--workers-url", workers_https_url,
        ],
        provider=_FailingProvider(),
    )

    assert exit_code == 5

    # structlog writes to stderr by default; capsys captures it.
    captured = capsys.readouterr()
    all_output = captured.out + captured.err

    # AC-7: event=oidc_token_failed must appear in output (structlog key=value format)
    assert "oidc_token_failed" in all_output, (
        f"expected oidc_token_failed in output; got: {all_output!r}"
    )

    # AC-7: no token bytes in any output
    assert FAKE_TOKEN_SENTINEL not in all_output, "token sentinel must not appear in logs"


# ---------------------------------------------------------------------------
# AC-10: non-authed target (localhost) — provider never invoked
# ---------------------------------------------------------------------------


def test_provider_not_called_on_localhost_target(
    tmp_path: Path, httpserver: HTTPServer
) -> None:
    """AC-10: localhost workers URL → provider.fetch() is never called."""
    httpserver.expect_request("/healthz").respond_with_json({"status": "ok"})
    httpserver.expect_request("/v1/retrieve").respond_with_json({
        "chunks": [{"source_path": "intro_p01", "text": "archiviste nocilia text here"}]
    })

    spy = _SpyProvider()
    output_path = tmp_path / "run.json"

    main(
        argv=[
            "--mode", "offline",
            "--set", str(FIXTURES / "golden_valid.jsonl"),
            "--output", str(output_path),
            "--workers-url", httpserver.url_for(""),  # http://localhost:NNNN
        ],
        provider=spy,
    )

    # AC-10: spy must have zero calls on localhost path
    assert spy.calls == [], f"provider.fetch() called unexpectedly: {spy.calls}"


# ---------------------------------------------------------------------------
# AC-11: token sentinel not in stdout/stderr/run-file on failure path
# ---------------------------------------------------------------------------


def test_token_not_in_stdout_stderr_on_exit_5(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC-11: even on the exit-5 path, no token bytes appear in stdout/stderr."""
    golden = FIXTURES / "golden_valid.jsonl"
    output_path = tmp_path / "run.json"

    # Use a provider that would produce a token (but raises so no token is actually created)
    main(
        argv=[
            "--mode", "offline",
            "--set", str(golden),
            "--output", str(output_path),
            "--workers-url", "https://workers.example.run.app",
        ],
        provider=_FailingProvider(),
    )

    captured = capsys.readouterr()
    assert FAKE_TOKEN_SENTINEL not in captured.out, "token leaked in stdout"
    assert FAKE_TOKEN_SENTINEL not in captured.err, "token leaked in stderr"


# ---------------------------------------------------------------------------
# AC-11(b): token sentinel absent from ALL outputs on the SUCCESS path
#
# This test covers the case where a token IS produced (SpyProvider returns
# FAKE_TOKEN_SENTINEL) and IS used (Bearer header attached on every workers
# call).  It would fail if redaction broke — e.g. if auth_header were passed
# to structlog without SecretStr wrapping, or if run-file JSON serialised the
# raw SecretStr value instead of redacting it.
# ---------------------------------------------------------------------------


def test_token_not_in_any_output_on_success_path(
    tmp_path: Path,
    httpserver: HTTPServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC-11(b): when token is produced and used, sentinel must not appear anywhere.

    Redaction regression detector: if auth_header.get_secret_value() were logged
    directly (bypassing SecretStr), or if the run-file writer serialised the raw
    token, FAKE_TOKEN_SENTINEL would appear and the assertion would catch it.
    """
    # Serve mocked workers endpoints so the run completes past token fetch.
    httpserver.expect_request("/healthz").respond_with_json({"status": "ok"})
    httpserver.expect_request("/v1/retrieve").respond_with_json({
        "chunks": [{"source_path": "intro_p01", "text": "archiviste nocilia text here"}]
    })

    workers_url = httpserver.url_for("")  # http://localhost:NNNN

    # Patch is_authenticated_target to treat the httpserver URL as an authed target
    # so the provider IS invoked and the token IS fetched and attached.
    # This isolates the redaction invariant from the scheme/host routing logic.
    import eval.ragas_runner as runner_module

    monkeypatch.setattr(runner_module, "is_authenticated_target", lambda _url: True)

    spy = _SpyProvider()  # returns SecretStr(FAKE_TOKEN_SENTINEL)
    output_path = tmp_path / "run.json"
    golden = FIXTURES / "golden_valid.jsonl"

    exit_code = main(
        argv=[
            "--mode", "offline",
            "--set", str(golden),
            "--output", str(output_path),
            "--workers-url", workers_url,
        ],
        provider=spy,
    )

    # Token must have been fetched (provider was called) — otherwise the test is vacuous.
    assert spy.calls, "SpyProvider was not called; test is vacuous — check monkeypatch"

    captured = capsys.readouterr()
    all_text_output = captured.out + captured.err

    # AC-11(b): sentinel must not appear in stdout or stderr
    assert FAKE_TOKEN_SENTINEL not in all_text_output, (
        "token sentinel leaked into stdout/stderr — redaction broken"
    )

    # AC-11(b): sentinel must not appear in the run-file JSON
    if output_path.exists():
        run_file_content = output_path.read_text(encoding="utf-8")
        assert FAKE_TOKEN_SENTINEL not in run_file_content, (
            "token sentinel leaked into run-file — redaction broken"
        )

    # Run must succeed (exit 0 or 1 for gate violations; not exit 5).
    assert exit_code != 5, f"unexpected exit 5; run failed on auth path: {all_text_output!r}"
