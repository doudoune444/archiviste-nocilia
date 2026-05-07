"""GEN-001 LLM wrapper unit tests (AC-8, AC-9, AC-25)."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from pydantic import SecretStr

from archiviste_workers.services.llm import (
    LlmConfig,
    LlmConfigError,
    _build_chat_model,
    extract_usage,
)


def test_fail_fast_provider_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # AC-8.
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_MODEL", "x")
    monkeypatch.setenv("LLM_API_KEY", "y")
    with pytest.raises(LlmConfigError):
        LlmConfig.from_env()


def test_fail_fast_provider_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    # AC-8.
    monkeypatch.setenv("LLM_PROVIDER", "unknown")
    monkeypatch.setenv("LLM_MODEL", "x")
    monkeypatch.setenv("LLM_API_KEY", "y")
    with pytest.raises(LlmConfigError):
        LlmConfig.from_env()


def test_fail_fast_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # AC-8.
    monkeypatch.setenv("LLM_PROVIDER", "mistral")
    monkeypatch.setenv("LLM_MODEL", "x")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(LlmConfigError):
        LlmConfig.from_env()


@pytest.mark.parametrize(
    ("provider", "expected_class"),
    [
        ("mistral", "ChatMistralAI"),
        ("anthropic", "ChatAnthropic"),
        ("google", "ChatGoogleGenerativeAI"),
        ("openai", "ChatOpenAI"),
        ("deepseek", "ChatDeepSeek"),
    ],
)
def test_dispatch_each_provider(provider: str, expected_class: str) -> None:
    # AC-9: each provider key resolves to the correct ChatModel subclass.
    config = LlmConfig(provider=provider, model="m", api_key=SecretStr("k"))
    chat = _build_chat_model(config)
    assert chat.__class__.__name__ == expected_class


def test_router_does_not_mention_provider_names() -> None:
    # AC-9 grep statique: router.py ne contient aucun nom de provider.
    router_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "archiviste_workers"
        / "generate"
        / "router.py"
    )
    text = router_path.read_text(encoding="utf-8")
    forbidden = (
        "mistral",
        "anthropic",
        "ChatMistralAI",
        "ChatAnthropic",
        "ChatOpenAI",
        "deepseek",
    )
    for token in forbidden:
        assert token.lower() not in text.lower()


def test_extract_usage_mistral() -> None:
    # AC-25.
    msg = AIMessage(
        content="x",
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    usage = extract_usage(msg, "mistral")
    assert usage.prompt_tokens == 10
    assert usage.completion_tokens == 5


def test_extract_usage_google() -> None:
    msg = AIMessage(
        content="x",
        usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
    )
    usage = extract_usage(msg, "google")
    assert usage.prompt_tokens == 7
    assert usage.completion_tokens == 3


def test_extract_usage_anthropic() -> None:
    msg = AIMessage(
        content="x",
        response_metadata={"usage": {"input_tokens": 11, "output_tokens": 4}},
    )
    usage = extract_usage(msg, "anthropic")
    assert usage.prompt_tokens == 11
    assert usage.completion_tokens == 4


def test_extract_usage_openai() -> None:
    msg = AIMessage(
        content="x",
        response_metadata={"token_usage": {"prompt_tokens": 13, "completion_tokens": 6}},
    )
    usage = extract_usage(msg, "openai")
    assert usage.prompt_tokens == 13
    assert usage.completion_tokens == 6


def test_extract_usage_deepseek() -> None:
    msg = AIMessage(
        content="x",
        response_metadata={"usage": {"prompt_tokens": 17, "completion_tokens": 8}},
    )
    usage = extract_usage(msg, "deepseek")
    assert usage.prompt_tokens == 17
    assert usage.completion_tokens == 8


def test_extract_usage_missing_returns_none() -> None:
    # AC-25: missing usage -> Usage(None, None).
    msg = AIMessage(content="x")
    usage = extract_usage(msg, "openai")
    assert usage.prompt_tokens is None
    assert usage.completion_tokens is None
