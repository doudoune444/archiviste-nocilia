"""GEN-001 pricing unit tests (AC-16)."""

from __future__ import annotations

from decimal import Decimal

from archiviste_workers.generate.pricing import MODEL_PRICING, compute_cost_eur


def test_compute_cost_known_model() -> None:
    # AC-16: cost = (p * prompt_eur_per_mtok + c * completion_eur_per_mtok) / 1e6.
    cost = compute_cost_eur("mistral-small-latest", 1_000_000, 1_000_000)
    assert (
        cost
        == MODEL_PRICING["mistral-small-latest"].prompt_eur_per_mtok
        + MODEL_PRICING["mistral-small-latest"].completion_eur_per_mtok
    )


def test_compute_cost_unknown_model_returns_none() -> None:
    # AC-16: unknown model -> cost_eur=null (no error).
    assert compute_cost_eur("unknown-model", 100, 50) is None


def test_compute_cost_missing_usage_returns_none() -> None:
    # AC-25 link: usage None -> cost None.
    assert compute_cost_eur("mistral-small-latest", None, 50) is None
    assert compute_cost_eur("mistral-small-latest", 50, None) is None


def test_compute_cost_quantization_six_decimals() -> None:
    # R4: NUMERIC(10,6) — quantize to 6 decimals.
    cost = compute_cost_eur("mistral-small-latest", 123, 456)
    assert isinstance(cost, Decimal)
    assert -cost.as_tuple().exponent == 6
