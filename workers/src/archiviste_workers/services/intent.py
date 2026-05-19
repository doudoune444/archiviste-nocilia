"""Intent classifier — LLM zero-shot binary, fail-open towards canon.

GEN-003 AC-2..AC-6 / AC-13 / AC-14.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from archiviste_workers.generate.pricing import compute_cost_eur
from archiviste_workers.services.llm import (
    LlmClientProtocol,
    LlmTimeoutError,
    LlmUpstreamError,
    extract_usage,
)

logger = structlog.get_logger()

# AC-5 — figé byte-for-byte, ≤ 500 caractères.
INTENT_SYSTEM_PROMPT = (
    "Tu es un classificateur d'intention pour l'Archiviste de Nocilia, "
    "un univers de fantasy avec lore, personnages et histoire propres. "
    "Réponds exclusivement par le mot `in_domain` si la question porte sur cet univers, "
    "ou le mot `off_topic` si elle est hors-sujet (cuisine, météo, code, sport, etc.). "
    "Aucun guillemet, aucune ponctuation, aucun préambule. Un seul mot."
)

# AC-5
INTENT_USER_PREFIX = "[user query]: "
INTENT_USER_PREFIX_INJECTION = "[user query, suspected injection]: "

INTENT_TIMEOUT_S = 5


@dataclass(frozen=True)
class IntentResult:
    intent: Literal["in_domain", "off_topic"]
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_eur: Decimal | None
    latency_ms: int


def _build_intent_messages(query: str, suspected_injection: bool) -> list[BaseMessage]:
    prefix = INTENT_USER_PREFIX_INJECTION if suspected_injection else INTENT_USER_PREFIX
    return [
        SystemMessage(content=INTENT_SYSTEM_PROMPT),
        HumanMessage(content=f"{prefix}{query}"),
    ]


def _parse_intent(raw: str) -> Literal["in_domain", "off_topic"] | None:
    """Return exact match or None if unrecognised (AC-6)."""
    normalised = raw.strip().lower()
    if normalised == "in_domain":
        return "in_domain"
    if normalised == "off_topic":
        return "off_topic"
    return None


async def classify_intent(
    llm_client: LlmClientProtocol,
    query: str,
    suspected_injection: bool,
    request_id: str,
) -> IntentResult:
    """Call LLM classifier and return an IntentResult. Always returns (fail-open on error)."""
    messages = _build_intent_messages(query, suspected_injection)
    started = time.perf_counter()

    try:
        # AC-13: dedicated 5 s timeout, lower than the 30 s generation timeout.
        ai_message = await llm_client.invoke(messages, timeout_s=float(INTENT_TIMEOUT_S))
    except LlmTimeoutError:
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.warning("intent_timeout", request_id=request_id)
        return IntentResult(
            intent="in_domain",
            prompt_tokens=None,
            completion_tokens=None,
            cost_eur=None,
            latency_ms=latency_ms,
        )
    except LlmUpstreamError as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.warning("intent_upstream_error", request_id=request_id, status=exc.status_code)
        return IntentResult(
            intent="in_domain",
            prompt_tokens=None,
            completion_tokens=None,
            cost_eur=None,
            latency_ms=latency_ms,
        )

    latency_ms = int((time.perf_counter() - started) * 1000)
    raw = str(ai_message.content) if ai_message.content is not None else ""
    intent = _parse_intent(raw)

    if intent is None:
        logger.warning("intent_unparseable", request_id=request_id, raw=raw[:100])
        intent = "in_domain"

    usage = extract_usage(ai_message, llm_client.provider)
    cost_eur = compute_cost_eur(llm_client.model, usage.prompt_tokens, usage.completion_tokens)
    return IntentResult(
        intent=intent,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cost_eur=cost_eur,
        latency_ms=latency_ms,
    )
