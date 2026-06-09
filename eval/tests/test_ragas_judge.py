"""Tests for build_ragas_judge() — AC-1..AC-7, AC-10."""

from __future__ import annotations

import io
import json
import logging
import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from eval.metrics import DEFAULT_MISTRAL_JUDGE_MODEL, build_ragas_judge
from eval.run_writer import RunFile, RunTotals, _build_run_dict

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_API_KEY = "sk-secret-test-key-do-not-log"
_FAKE_OAI_KEY = "sk-openai-fake-key"


def _set_env(**kwargs: str) -> Any:
    """Context manager: set env vars for test duration."""
    return patch.dict(os.environ, kwargs)


# ---------------------------------------------------------------------------
# AC-1 : unset RAGAS_JUDGE_PROVIDER → mistral couple returned
# ---------------------------------------------------------------------------


def test_build_judge_default_is_mistral() -> None:
    """AC-1: absent RAGAS_JUDGE_PROVIDER → Mistral judge."""
    # Ensure RAGAS_JUDGE_PROVIDER is unset; LLM_API_KEY is required by the builder.
    with patch.dict(os.environ, {"LLM_API_KEY": _FAKE_API_KEY}):
        os.environ.pop("RAGAS_JUDGE_PROVIDER", None)
        llm, embeddings = build_ragas_judge()
    assert llm is not None
    assert embeddings is not None


# ---------------------------------------------------------------------------
# AC-2 : RAGAS_JUDGE_PROVIDER=mistral → LangchainLLMWrapper(ChatMistralAI) + MistralAIEmbeddings
# ---------------------------------------------------------------------------


def test_build_judge_mistral_llm_type() -> None:
    """AC-2: mistral provider → LangchainLLMWrapper wrapping a ChatMistralAI."""
    from ragas.llms.base import LangchainLLMWrapper

    with _set_env(RAGAS_JUDGE_PROVIDER="mistral", LLM_API_KEY=_FAKE_API_KEY):
        llm, _ = build_ragas_judge()

    assert isinstance(llm, LangchainLLMWrapper)
    from langchain_mistralai import ChatMistralAI

    assert isinstance(llm.langchain_llm, ChatMistralAI)


def test_build_judge_mistral_embeddings_type() -> None:
    """AC-2: mistral provider → LangchainEmbeddingsWrapper wrapping MistralAIEmbeddings."""
    from ragas.embeddings.base import LangchainEmbeddingsWrapper

    with _set_env(RAGAS_JUDGE_PROVIDER="mistral", LLM_API_KEY=_FAKE_API_KEY):
        _, embeddings = build_ragas_judge()

    assert isinstance(embeddings, LangchainEmbeddingsWrapper)
    from langchain_mistralai import MistralAIEmbeddings

    assert isinstance(embeddings.embeddings, MistralAIEmbeddings)


# ---------------------------------------------------------------------------
# AC-3 : RAGAS_JUDGE_PROVIDER=openai → LangchainLLMWrapper(ChatOpenAI) + OpenAIEmbeddings
# ---------------------------------------------------------------------------


def test_build_judge_openai_llm_type() -> None:
    """AC-3: openai provider → LangchainLLMWrapper wrapping a ChatOpenAI."""
    from ragas.llms.base import LangchainLLMWrapper

    with _set_env(RAGAS_JUDGE_PROVIDER="openai", LLM_API_KEY=_FAKE_OAI_KEY):
        llm, _ = build_ragas_judge()

    assert isinstance(llm, LangchainLLMWrapper)
    from langchain_openai import ChatOpenAI

    assert isinstance(llm.langchain_llm, ChatOpenAI)


def test_build_judge_openai_embeddings_type() -> None:
    """AC-3: openai provider → LangchainEmbeddingsWrapper wrapping OpenAIEmbeddings."""
    from ragas.embeddings.base import LangchainEmbeddingsWrapper

    with _set_env(RAGAS_JUDGE_PROVIDER="openai", LLM_API_KEY=_FAKE_OAI_KEY):
        _, embeddings = build_ragas_judge()

    assert isinstance(embeddings, LangchainEmbeddingsWrapper)
    from langchain_openai import OpenAIEmbeddings

    assert isinstance(embeddings.embeddings, OpenAIEmbeddings)


# ---------------------------------------------------------------------------
# AC-4 : unknown provider → ValueError, no ragas.evaluate() call
# ---------------------------------------------------------------------------


def test_build_judge_unknown_provider_raises() -> None:
    """AC-4: unknown provider → ValueError with received value + allowed set."""
    with (
        _set_env(RAGAS_JUDGE_PROVIDER="anthropic", LLM_API_KEY=_FAKE_API_KEY),
        pytest.raises(ValueError) as exc_info,
    ):
        build_ragas_judge()

    msg = str(exc_info.value)
    assert "anthropic" in msg
    assert "mistral" in msg
    assert "openai" in msg


def test_unknown_provider_no_ragas_evaluate_call() -> None:
    """AC-4: unknown provider → build_ragas_judge raises before any judge objects are built."""
    # Assert that the error propagates immediately: no llm/embeddings are returned.
    with (
        _set_env(RAGAS_JUDGE_PROVIDER="anthropic", LLM_API_KEY=_FAKE_API_KEY),
        pytest.raises(ValueError) as exc_info,
    ):
        build_ragas_judge()
    # Confirm the ValueError (not RuntimeError or other) is the one that stops the path.
    assert isinstance(exc_info.value, ValueError)


# ---------------------------------------------------------------------------
# AC-5 : ragas.evaluate() called with llm= and embeddings= from build_ragas_judge()
# ---------------------------------------------------------------------------


def test_run_ragas_evaluate_passes_judge_to_ragas() -> None:
    """AC-5: _run_ragas_evaluate passes llm= and embeddings= from build_ragas_judge()."""
    import pandas as pd

    from eval.metrics import _run_ragas_evaluate
    from eval.run_writer import EntryResult

    entries = [
        EntryResult(
            id="q1",
            mode="canon",
            question="What is Nocilia?",
            status="ok",
            answer="A city.",
            ground_truth="city",
            retrieved_contexts=["doc/intro.md"],
        )
    ]

    captured_kwargs: dict[str, Any] = {}

    def fake_evaluate(dataset: Any, metrics: Any = None, **kwargs: Any) -> Any:
        captured_kwargs.update(kwargs)
        mock_result = MagicMock()
        mock_result.to_pandas.return_value = pd.DataFrame(
            {
                "faithfulness": [0.9],
                "answer_relevancy": [0.85],
                "context_precision": [0.8],
                "context_recall": [0.75],
            }
        )
        return mock_result

    with (
        _set_env(RAGAS_JUDGE_PROVIDER="mistral", LLM_API_KEY=_FAKE_API_KEY),
        patch("ragas.evaluate", fake_evaluate),
    ):
        _run_ragas_evaluate(entries)

    # AC-5 oracle: objects passed must match shape produced by build_ragas_judge().
    from ragas.embeddings.base import LangchainEmbeddingsWrapper
    from ragas.llms.base import LangchainLLMWrapper

    assert isinstance(captured_kwargs.get("llm"), LangchainLLMWrapper), (
        "ragas.evaluate must be called with llm= of type LangchainLLMWrapper"
    )
    assert isinstance(captured_kwargs.get("embeddings"), LangchainEmbeddingsWrapper), (
        "ragas.evaluate must be called with embeddings= of type LangchainEmbeddingsWrapper"
    )


# ---------------------------------------------------------------------------
# AC-6 : pinned default model + overrides
# ---------------------------------------------------------------------------


def test_build_judge_mistral_default_chat_model() -> None:
    """AC-6: without override, ChatMistralAI uses the pinned snapshot DEFAULT_MISTRAL_JUDGE_MODEL."""
    from langchain_mistralai import ChatMistralAI

    with patch.dict(os.environ, {"LLM_API_KEY": _FAKE_API_KEY, "RAGAS_JUDGE_PROVIDER": "mistral"}):
        os.environ.pop("RAGAS_JUDGE_MODEL", None)
        llm, _ = build_ragas_judge()

    assert isinstance(llm.langchain_llm, ChatMistralAI)
    assert llm.langchain_llm.model == DEFAULT_MISTRAL_JUDGE_MODEL


def test_build_judge_mistral_default_embeddings_model() -> None:
    """AC-6: without override, MistralAIEmbeddings uses 'mistral-embed'."""
    from langchain_mistralai import MistralAIEmbeddings

    with patch.dict(os.environ, {"LLM_API_KEY": _FAKE_API_KEY, "RAGAS_JUDGE_PROVIDER": "mistral"}):
        os.environ.pop("RAGAS_JUDGE_EMBEDDINGS_MODEL", None)
        _, embeddings = build_ragas_judge()

    assert isinstance(embeddings.embeddings, MistralAIEmbeddings)
    assert embeddings.embeddings.model == "mistral-embed"


def test_build_judge_mistral_model_override() -> None:
    """AC-6: RAGAS_JUDGE_MODEL override is applied to ChatMistralAI."""
    from langchain_mistralai import ChatMistralAI

    with _set_env(
        RAGAS_JUDGE_PROVIDER="mistral",
        LLM_API_KEY=_FAKE_API_KEY,
        RAGAS_JUDGE_MODEL="mistral-large-latest",
    ):
        llm, _ = build_ragas_judge()

    assert isinstance(llm.langchain_llm, ChatMistralAI)
    assert llm.langchain_llm.model == "mistral-large-latest"


def test_build_judge_mistral_embeddings_model_override() -> None:
    """AC-6: RAGAS_JUDGE_EMBEDDINGS_MODEL override is applied to MistralAIEmbeddings."""
    from langchain_mistralai import MistralAIEmbeddings

    with _set_env(
        RAGAS_JUDGE_PROVIDER="mistral",
        LLM_API_KEY=_FAKE_API_KEY,
        RAGAS_JUDGE_EMBEDDINGS_MODEL="custom-embed-model",
    ):
        _, embeddings = build_ragas_judge()

    assert isinstance(embeddings.embeddings, MistralAIEmbeddings)
    assert embeddings.embeddings.model == "custom-embed-model"


# ---------------------------------------------------------------------------
# AC-7 : LLM_API_KEY never leaks through repr / logs / run dict
# ---------------------------------------------------------------------------


def test_api_key_absent_from_llm_repr() -> None:
    """AC-7: secret key must not appear in repr() of the judge LLM object."""
    with _set_env(RAGAS_JUDGE_PROVIDER="mistral", LLM_API_KEY=_FAKE_API_KEY):
        llm, _ = build_ragas_judge()

    assert _FAKE_API_KEY not in repr(llm)


def test_api_key_absent_from_embeddings_repr() -> None:
    """AC-7: secret key must not appear in repr() of the judge embeddings object."""
    with _set_env(RAGAS_JUDGE_PROVIDER="mistral", LLM_API_KEY=_FAKE_API_KEY):
        _, embeddings = build_ragas_judge()

    assert _FAKE_API_KEY not in repr(embeddings)


def test_api_key_absent_from_captured_logs() -> None:
    """AC-7: secret key must not appear in any log output during judge construction."""
    log_capture = io.StringIO()
    handler = logging.StreamHandler(log_capture)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    try:
        with _set_env(RAGAS_JUDGE_PROVIDER="mistral", LLM_API_KEY=_FAKE_API_KEY):
            build_ragas_judge()
    finally:
        root_logger.removeHandler(handler)

    assert _FAKE_API_KEY not in log_capture.getvalue()


# ---------------------------------------------------------------------------
# AC-10 : _build_run_dict includes judge field when set
# ---------------------------------------------------------------------------


def _make_run_file(judge: dict[str, str] | None) -> RunFile:
    return RunFile(
        mode="live",
        started_at="2026-06-09T00:00:00+00:00",
        finished_at="2026-06-09T00:01:00+00:00",
        git_sha="abc1234",
        runner_mode="live",
        totals=RunTotals(entries=1, ok=1, errors=0),
        breakdown_by_mode={},
        metrics={
            "faithfulness": 0.9,
            "answer_relevancy": 0.85,
            "context_precision": 0.8,
            "context_recall": 0.75,
        },
        entries=[],
        judge=judge,
    )


def test_build_run_dict_judge_field_present() -> None:
    """AC-10: run dict contains judge field with provider and chat model id."""
    expected_judge = {"provider": "mistral", "chat_model": DEFAULT_MISTRAL_JUDGE_MODEL}
    run = _make_run_file(judge=expected_judge)
    run_dict = _build_run_dict(run)

    assert "judge" in run_dict
    assert run_dict["judge"] == expected_judge


def test_build_run_dict_judge_field_absent_when_none() -> None:
    """AC-10: judge field absent from run dict when judge=None (offline mode)."""
    run = _make_run_file(judge=None)
    run_dict = _build_run_dict(run)

    assert "judge" not in run_dict


def test_build_run_dict_judge_no_api_key() -> None:
    """AC-10 / AC-7: judge field must never contain the API key."""
    judge = {"provider": "mistral", "chat_model": DEFAULT_MISTRAL_JUDGE_MODEL}
    run = _make_run_file(judge=judge)
    run_dict = _build_run_dict(run)

    serialized = json.dumps(run_dict)
    assert _FAKE_API_KEY not in serialized


# ---------------------------------------------------------------------------
# MED regression: openai path must record openai chat model id, not mistral default
# ---------------------------------------------------------------------------


def test_run_ragas_evaluate_openai_judge_identity_records_openai_model() -> None:
    """MED regression (AC-10): openai provider → judge_identity.chat_model = openai model id.

    Prevents the bug where _run_ragas_evaluate re-derived identity from env independently
    of the builder, causing DEFAULT_MISTRAL_JUDGE_MODEL to be recorded even when the
    openai builder resolved 'gpt-4o'.
    """
    import pandas as pd

    from eval.metrics import _run_ragas_evaluate
    from eval.run_writer import EntryResult

    entries = [
        EntryResult(
            id="q1",
            mode="canon",
            question="What is Nocilia?",
            status="ok",
            answer="A city.",
            ground_truth="city",
            retrieved_contexts=["doc/intro.md"],
        )
    ]

    def fake_evaluate(dataset: Any, metrics: Any = None, **kwargs: Any) -> Any:
        mock_result = MagicMock()
        mock_result.to_pandas.return_value = pd.DataFrame(
            {
                "faithfulness": [0.9],
                "answer_relevancy": [0.85],
                "context_precision": [0.8],
                "context_recall": [0.75],
            }
        )
        return mock_result

    with (
        _set_env(RAGAS_JUDGE_PROVIDER="openai", LLM_API_KEY=_FAKE_OAI_KEY),
        patch("ragas.evaluate", fake_evaluate),
    ):
        os.environ.pop("RAGAS_JUDGE_MODEL", None)
        _, judge_identity = _run_ragas_evaluate(entries)

    assert judge_identity is not None
    assert judge_identity["provider"] == "openai"
    # Must record the openai default model, not DEFAULT_MISTRAL_JUDGE_MODEL.
    assert judge_identity["chat_model"] == "gpt-4o", (
        f"expected 'gpt-4o', got {judge_identity['chat_model']!r} — "
        "judge identity must be sourced from the builder, not re-derived from env"
    )
    assert judge_identity["chat_model"] != DEFAULT_MISTRAL_JUDGE_MODEL, (
        "openai path must not record the mistral default model"
    )
