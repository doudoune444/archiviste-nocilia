"""LLM wrapper config-driven (AC-8/9/11/25). Single dispatch site for providers."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_deepseek import ChatDeepSeek
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_mistralai import ChatMistralAI
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from archiviste_workers.generate.models import Usage

if TYPE_CHECKING:  # pragma: no cover
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, BaseMessage

logger = structlog.get_logger()

LLM_TIMEOUT_S = 30
_PROVIDERS = ("mistral", "anthropic", "google", "openai", "deepseek")


class LlmConfigError(RuntimeError):
    """Raised at boot when LLM_* env is missing or invalid (AC-8)."""


class LlmUpstreamError(RuntimeError):
    """Provider 4xx/5xx (AC-21/22). status_code attached for routing."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class LlmTimeoutError(RuntimeError):
    """Hard 30 s timeout exceeded (AC-11)."""


@dataclass(frozen=True)
class LlmConfig:
    provider: str
    model: str
    api_key: SecretStr

    @classmethod
    def from_env(cls) -> LlmConfig:
        provider = os.environ.get("LLM_PROVIDER", "").strip()
        model = os.environ.get("LLM_MODEL", "").strip()
        api_key = os.environ.get("LLM_API_KEY", "").strip()
        if provider not in _PROVIDERS:
            raise LlmConfigError(
                f"LLM_PROVIDER missing or invalid (got {provider!r}, expected one of {_PROVIDERS})"
            )
        if not model:
            raise LlmConfigError("LLM_MODEL missing or empty")
        if not api_key:
            raise LlmConfigError("LLM_API_KEY missing or empty")
        return cls(provider=provider, model=model, api_key=SecretStr(api_key))


def _build_chat_model(config: LlmConfig) -> BaseChatModel:
    key = config.api_key.get_secret_value()
    if config.provider == "mistral":
        return ChatMistralAI(model=config.model, api_key=SecretStr(key), timeout=LLM_TIMEOUT_S)
    if config.provider == "anthropic":
        return ChatAnthropic(model=config.model, api_key=SecretStr(key), timeout=LLM_TIMEOUT_S)
    if config.provider == "google":
        return ChatGoogleGenerativeAI(model=config.model, google_api_key=key, timeout=LLM_TIMEOUT_S)
    if config.provider == "openai":
        return ChatOpenAI(model=config.model, api_key=SecretStr(key), timeout=LLM_TIMEOUT_S)
    if config.provider == "deepseek":
        return ChatDeepSeek(model_name=config.model, api_key=SecretStr(key), timeout=LLM_TIMEOUT_S)
    raise LlmConfigError(f"unsupported provider {config.provider!r}")  # pragma: no cover


class LlmClient:
    """Provider-agnostic invoker. Router never references provider names (AC-9)."""

    def __init__(self, config: LlmConfig, chat_model: BaseChatModel) -> None:
        self._config = config
        self._chat = chat_model

    @classmethod
    def from_env(cls) -> LlmClient:
        config = LlmConfig.from_env()
        return cls(config, _build_chat_model(config))

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def provider(self) -> str:
        return self._config.provider

    async def invoke(self, messages: list[BaseMessage]) -> AIMessage:
        try:
            return await asyncio.wait_for(self._chat.ainvoke(messages), timeout=LLM_TIMEOUT_S)
        except TimeoutError as exc:
            raise LlmTimeoutError("llm timeout") from exc
        except Exception as exc:
            status = _extract_status(exc)
            raise LlmUpstreamError(str(exc), status_code=status) from exc


def _extract_status(exc: BaseException) -> int:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    if response is not None:
        rstatus = getattr(response, "status_code", None)
        if isinstance(rstatus, int):
            return rstatus
    return 500


def extract_usage(message: AIMessage, provider: str) -> Usage:
    """Normalize per-provider usage shape (AC-25)."""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    meta = getattr(message, "usage_metadata", None) or {}
    if provider in {"mistral", "google"} and meta:
        prompt_tokens = meta.get("input_tokens")
        completion_tokens = meta.get("output_tokens")
    else:
        response_meta = getattr(message, "response_metadata", None) or {}
        if provider == "anthropic":
            usage = response_meta.get("usage", {}) or {}
            prompt_tokens = usage.get("input_tokens")
            completion_tokens = usage.get("output_tokens")
        elif provider == "openai":
            usage = response_meta.get("token_usage", {}) or {}
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
        elif provider == "deepseek":
            usage = response_meta.get("usage", {}) or {}
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
    if prompt_tokens is None or completion_tokens is None:
        logger.warning("usage_missing", provider=provider)
        return Usage(prompt_tokens=None, completion_tokens=None)
    return Usage(prompt_tokens=int(prompt_tokens), completion_tokens=int(completion_tokens))
