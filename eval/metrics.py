"""Metric computation: deterministic (all modes) + Ragas (canon, live only)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import SecretStr
    from ragas.embeddings.base import LangchainEmbeddingsWrapper
    from ragas.llms.base import LangchainLLMWrapper

    from eval.run_writer import EntryResult

# Pinned dated snapshot — stable scoring, no silent drift (OQ-1: confirmed mistral-large-2411).
# Override via RAGAS_JUDGE_MODEL; embeddings default via RAGAS_JUDGE_EMBEDDINGS_MODEL.
DEFAULT_MISTRAL_JUDGE_MODEL = "mistral-large-2411"
DEFAULT_MISTRAL_EMBEDDINGS_MODEL = "mistral-embed"

# Ragas concurrency throttle: ragas.evaluate() defaults to max_workers=16 which floods
# Mistral with 16 concurrent requests → HTTP 429 storms → retry backoff exceeds the
# Cloud Run Job task timeout (600 s). 3 workers stays within Mistral's per-second limit;
# backoff values absorb remaining spikes (EVAL-008).
RAGAS_MAX_WORKERS = 3
RAGAS_CALL_TIMEOUT_SECONDS = 180
RAGAS_MAX_RETRIES = 10
RAGAS_MAX_WAIT_SECONDS = 60


def compute_keyword_overlap(answer: str, keywords: list[str]) -> bool:
    """Return True if answer contains at least one keyword (case-insensitive, AC-7)."""
    answer_lower = answer.lower()
    return any(kw.lower() in answer_lower for kw in keywords)


def compute_context_recall_structural(
    expected_contexts: list[str],
    retrieved_contexts: list[str],
) -> float:
    """Fraction of expected_contexts present in retrieved_contexts (AC-8).

    Match is exact on source_path string.
    Returns 0.0 if expected_contexts is empty.
    """
    if not expected_contexts:
        return 0.0
    retrieved_set = set(retrieved_contexts)
    hits = sum(1 for ctx in expected_contexts if ctx in retrieved_set)
    return hits / len(expected_contexts)


def aggregate_breakdown(entries: list[EntryResult]) -> dict[str, object]:
    """Aggregate per-mode breakdown metrics (AC-5, AC-7, AC-8)."""
    modes = ("canon", "off_topic", "lore_gap", "mystery")
    breakdown: dict[str, object] = {}
    for mode in modes:
        mode_entries = [e for e in entries if e.mode == mode]
        if not mode_entries:
            breakdown[mode] = {"entries": 0, "keyword_overlap_rate": None}
            continue
        overlap_hits = sum(
            1 for e in mode_entries if e.metrics.get("keyword_overlap_rate") == 1.0
        )
        overlap_rate = overlap_hits / len(mode_entries)
        mode_data: dict[str, object] = {
            "entries": len(mode_entries),
            "keyword_overlap_rate": overlap_rate,
        }
        if mode == "canon":
            recall_values: list[float] = [
                float(e.metrics["context_recall_structural"])
                for e in mode_entries
                if "context_recall_structural" in e.metrics
                and e.metrics["context_recall_structural"] is not None
            ]
            mode_data["context_recall_structural"] = (
                sum(recall_values) / len(recall_values) if recall_values else 0.0
            )
        breakdown[mode] = mode_data
    return breakdown


def build_ragas_judge() -> tuple[LangchainLLMWrapper, LangchainEmbeddingsWrapper]:
    """Build the Ragas judge (llm, embeddings) from env configuration.

    Provider is selected by RAGAS_JUDGE_PROVIDER (default: mistral).
    API key is read from LLM_API_KEY via SecretStr — never logged or serialized.
    All heavy imports are lazy (inside this function) to keep module import dep-free.

    Returns:
        (LangchainLLMWrapper, LangchainEmbeddingsWrapper) for ragas.evaluate(llm=, embeddings=).

    Raises:
        ValueError: when RAGAS_JUDGE_PROVIDER is not in {mistral, openai}.
    """
    llm, embeddings, _, _ = _build_ragas_judge_with_identity()
    return llm, embeddings


def _build_ragas_judge_with_identity() -> (
    tuple[LangchainLLMWrapper, LangchainEmbeddingsWrapper, str, str]
):
    """Build the judge and return (llm, embeddings, provider, chat_model_id).

    Single source of truth for resolved judge identity — callers must not
    re-derive provider/model from env independently.
    All heavy imports are lazy (inside builders) to keep module import dep-free.

    Raises:
        ValueError: when RAGAS_JUDGE_PROVIDER is not in {mistral, openai}.
    """
    try:
        from pydantic import SecretStr  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "pydantic is required for live mode judge; install [live] extras"
        ) from exc

    provider = os.environ.get("RAGAS_JUDGE_PROVIDER", "mistral")
    api_key: SecretStr = SecretStr(os.environ.get("LLM_API_KEY", ""))

    if provider == "mistral":
        llm, embeddings, chat_model_id = _build_mistral_judge(api_key)
        return llm, embeddings, provider, chat_model_id
    if provider == "openai":
        llm, embeddings, chat_model_id = _build_openai_judge(api_key)
        return llm, embeddings, provider, chat_model_id
    raise ValueError(
        f"Unknown RAGAS_JUDGE_PROVIDER: received={provider!r} allowed=mistral|openai"
    )


def _build_mistral_judge(
    api_key: SecretStr,
) -> tuple[LangchainLLMWrapper, LangchainEmbeddingsWrapper, str]:
    """Build Mistral judge couple. All imports lazy (PLC0415).

    Returns (llm_wrapper, embeddings_wrapper, chat_model_id).
    """
    try:
        from langchain_mistralai import ChatMistralAI, MistralAIEmbeddings  # noqa: PLC0415
        from ragas.embeddings.base import LangchainEmbeddingsWrapper  # noqa: PLC0415
        from ragas.llms.base import LangchainLLMWrapper  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "langchain-mistralai and ragas are required for mistral judge; install [live] extras"
        ) from exc

    chat_model = os.environ.get("RAGAS_JUDGE_MODEL", DEFAULT_MISTRAL_JUDGE_MODEL)
    emb_model = os.environ.get("RAGAS_JUDGE_EMBEDDINGS_MODEL", DEFAULT_MISTRAL_EMBEDDINGS_MODEL)

    # model_name= is the pydantic alias for the `model` field (api_key= for mistral_api_key).
    chat_llm = ChatMistralAI(model_name=chat_model, api_key=api_key)
    embeddings = MistralAIEmbeddings(model=emb_model, api_key=api_key)
    return LangchainLLMWrapper(chat_llm), LangchainEmbeddingsWrapper(embeddings), chat_model


def _build_openai_judge(
    api_key: SecretStr,
) -> tuple[LangchainLLMWrapper, LangchainEmbeddingsWrapper, str]:
    """Build OpenAI judge couple. All imports lazy (PLC0415).

    Returns (llm_wrapper, embeddings_wrapper, chat_model_id).
    """
    try:
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings  # noqa: PLC0415
        from ragas.embeddings.base import LangchainEmbeddingsWrapper  # noqa: PLC0415
        from ragas.llms.base import LangchainLLMWrapper  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "langchain-openai and ragas are required for openai judge; install [live] extras"
        ) from exc

    chat_model = os.environ.get("RAGAS_JUDGE_MODEL", "gpt-4o")
    emb_model = os.environ.get("RAGAS_JUDGE_EMBEDDINGS_MODEL", "text-embedding-3-small")

    chat_llm = ChatOpenAI(model=chat_model, api_key=api_key)
    embeddings = OpenAIEmbeddings(model=emb_model, api_key=api_key)
    return LangchainLLMWrapper(chat_llm), LangchainEmbeddingsWrapper(embeddings), chat_model


def compute_ragas_metrics(
    entries: list[EntryResult],
) -> tuple[dict[str, float | None], dict[str, str] | None]:
    """Compute Ragas metrics for canon entries (live mode only).

    Returns (metrics_dict, judge_identity) where judge_identity is None for empty entries.
    Ragas.evaluate() requires a real LLM judge; offline must not call this with entries.
    """
    if not entries:
        return (
            {
                "faithfulness": None,
                "answer_relevancy": None,
                "context_precision": None,
                "context_recall": None,
            },
            None,
        )

    return _run_ragas_evaluate(entries)


def _run_ragas_evaluate(
    entries: list[EntryResult],
) -> tuple[dict[str, float | None], dict[str, str] | None]:
    """Call ragas.evaluate() with the configured judge (live mode).

    Returns (scores, judge_identity) where judge_identity records provider + chat_model.
    """
    try:
        import datasets  # noqa: PLC0415
        import ragas  # noqa: PLC0415
        import ragas.metrics  # noqa: PLC0415
        from ragas.run_config import RunConfig  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "ragas and datasets are required for live mode metrics; "
            "install workers[dev] extras"
        ) from exc

    # Use the identity-aware builder so provider + chat_model_id come from a single source.
    llm, embeddings, provider, chat_model = _build_ragas_judge_with_identity()
    judge_identity: dict[str, str] = {"provider": provider, "chat_model": chat_model}

    dataset = datasets.Dataset.from_list(
        [
            {
                "question": e.question,
                "answer": e.answer or "",
                "contexts": e.retrieved_contexts,
                "ground_truth": e.ground_truth or "",
            }
            for e in entries
        ]
    )
    run_config = RunConfig(
        max_workers=RAGAS_MAX_WORKERS,
        timeout=RAGAS_CALL_TIMEOUT_SECONDS,
        max_retries=RAGAS_MAX_RETRIES,
        max_wait=RAGAS_MAX_WAIT_SECONDS,
    )
    eval_result = ragas.evaluate(
        dataset,
        metrics=[
            ragas.metrics.Faithfulness(),
            ragas.metrics.AnswerRelevancy(),
            ragas.metrics.ContextPrecision(),
            ragas.metrics.ContextRecall(),
        ],
        llm=llm,
        embeddings=embeddings,
        run_config=run_config,
    )
    # EvaluationResult.to_pandas() produces a DataFrame with per-metric columns.
    scores = eval_result.to_pandas()  # type: ignore[union-attr]
    return (
        {
            "faithfulness": float(scores["faithfulness"].mean()),
            "answer_relevancy": float(scores["answer_relevancy"].mean()),
            "context_precision": float(scores["context_precision"].mean()),
            "context_recall": float(scores["context_recall"].mean()),
        },
        judge_identity,
    )
