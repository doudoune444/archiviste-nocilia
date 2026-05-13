"""Run file writer with AC-5 schema and AC-16 secret redaction."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

SECRET_ENV_VARS = ("LLM_API_KEY", "DATABASE_URL", "WORKERS_URL")


@dataclass
class EntryMetrics:
    """Per-entry metric values."""

    keyword_overlap_rate: float | None = None
    context_recall_structural: float | None = None


@dataclass
class EntryResult:
    """Single evaluated entry stored in the run file."""

    id: str
    mode: str
    question: str
    status: Literal["ok", "timeout", "upstream_error", "malformed"]
    metrics: dict[str, float | None] = field(default_factory=dict)
    retrieved_contexts: list[str] = field(default_factory=list)
    answer: str | None = None
    citations: list[str] = field(default_factory=list)
    request_id: str = ""
    ground_truth: str | None = None


@dataclass
class RunTotals:
    """Summary counts."""

    entries: int
    ok: int
    errors: int


@dataclass
class RunFile:
    """Full run output file (AC-5 schema)."""

    mode: str
    started_at: str
    finished_at: str
    git_sha: str
    runner_mode: Literal["live", "offline"]
    totals: RunTotals
    breakdown_by_mode: dict[str, object]
    metrics: dict[str, float | None]
    entries: list[EntryResult]


def _collect_secret_values() -> list[str]:
    """Collect non-empty secret env var values for redaction (AC-16)."""
    secrets: list[str] = []
    for var in SECRET_ENV_VARS:
        value = os.environ.get(var, "")
        if value:
            secrets.append(value)
    return secrets


def _redact_string(text: str, secrets: list[str]) -> str:
    """Replace secret values in text with [REDACTED]."""
    for secret in secrets:
        text = text.replace(secret, "[REDACTED]")
    return text


def _redact_entry(entry_dict: dict[str, object], secrets: list[str]) -> dict[str, object]:
    """Redact secrets from a serialized entry dict."""
    redacted = dict(entry_dict)
    answer = redacted.get("answer")
    if isinstance(answer, str):
        redacted["answer"] = _redact_string(answer, secrets)
    return redacted


def _build_run_dict(run: RunFile) -> dict[str, object]:
    """Serialize RunFile to a plain dict matching AC-5 schema."""
    return {
        "mode": run.mode,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "git_sha": run.git_sha,
        "runner_mode": run.runner_mode,
        "totals": {
            "entries": run.totals.entries,
            "ok": run.totals.ok,
            "errors": run.totals.errors,
        },
        "breakdown_by_mode": run.breakdown_by_mode,
        "metrics": run.metrics,
        "entries": [
            {
                "id": e.id,
                "mode": e.mode,
                "status": e.status,
                "metrics": e.metrics,
                "retrieved_contexts": e.retrieved_contexts,
                "answer": e.answer,
                "citations": e.citations,
                "request_id": e.request_id,
            }
            for e in run.entries
        ],
    }


def write_run(path: Path, run: RunFile) -> None:
    """Serialize RunFile to JSON with secret redaction (AC-16)."""
    secrets = _collect_secret_values()
    run_dict = _build_run_dict(run)

    raw = json.dumps(run_dict, indent=2, ensure_ascii=False)
    for secret in secrets:
        if secret:
            raw = raw.replace(re.escape(secret) if False else secret, "[REDACTED]")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
