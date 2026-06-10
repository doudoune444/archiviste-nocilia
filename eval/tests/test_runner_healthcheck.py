"""EVAL-006: workers reachability probe must target /health, not /healthz.

Cloud Run's frontend reserves the literal /healthz path and 404s it before the
container, so probing /healthz always reports the service unreachable. The probe
must also tolerate workers cold-start (~20s), hence the 30s timeout.
"""

from __future__ import annotations

from typing import Any

import pytest

import eval.ragas_runner as runner


class _StubResponse:
    is_success = True


def test_health_probe_uses_health_path_and_cold_start_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(url: str, **kwargs: Any) -> _StubResponse:
        captured["url"] = url
        captured["timeout"] = kwargs.get("timeout")
        return _StubResponse()

    monkeypatch.setattr("eval.ragas_runner.httpx.get", _fake_get)

    assert runner._check_workers_reachable("https://workers.example.run.app") is True
    assert captured["url"].endswith("/health")
    assert not captured["url"].endswith("/healthz")
    assert captured["timeout"] == 30.0
