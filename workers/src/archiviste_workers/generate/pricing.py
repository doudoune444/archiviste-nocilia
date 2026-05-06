"""LLM pricing table (AC-16). Hardcoded phase 1; YAML migration when 2nd model lands."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal


@dataclass(frozen=True)
class ModelPricing:
    prompt_eur_per_mtok: Decimal
    completion_eur_per_mtok: Decimal


# Prices per million tokens (EUR). Source: provider public price pages 2026-Q2.
MODEL_PRICING: dict[str, ModelPricing] = {
    "mistral-small-latest": ModelPricing(Decimal("0.20"), Decimal("0.60")),
    "mistral-large-latest": ModelPricing(Decimal("2.00"), Decimal("6.00")),
    "claude-3-5-sonnet-20241022": ModelPricing(Decimal("2.80"), Decimal("14.00")),
    "gemini-1.5-flash": ModelPricing(Decimal("0.07"), Decimal("0.28")),
    "gpt-4o-mini": ModelPricing(Decimal("0.14"), Decimal("0.56")),
    "deepseek-chat": ModelPricing(Decimal("0.13"), Decimal("0.27")),
}

_QUANT = Decimal("0.000001")  # NUMERIC(10,6) per query_log schema (R4).


def compute_cost_eur(
    model: str, prompt_tokens: int | None, completion_tokens: int | None
) -> Decimal | None:
    """Return EUR cost rounded to 6 decimals, or None if model unknown / usage missing."""
    if prompt_tokens is None or completion_tokens is None:
        return None
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        return None
    cost = (
        pricing.prompt_eur_per_mtok * Decimal(prompt_tokens)
        + pricing.completion_eur_per_mtok * Decimal(completion_tokens)
    ) / Decimal(1_000_000)
    return cost.quantize(_QUANT, rounding=ROUND_HALF_EVEN)
