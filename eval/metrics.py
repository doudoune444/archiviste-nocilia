"""Metric computation: deterministic (all modes) + Ragas (canon, live only)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eval.run_writer import EntryResult


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


def compute_ragas_metrics(
    entries: list[EntryResult],
) -> dict[str, float | None]:
    """Compute Ragas metrics for canon entries (live mode only).

    Returns null values when entries list is empty (offline mode callers pass []).
    Ragas.evaluate() requires a real LLM judge; offline must not call this with entries.
    """
    if not entries:
        return {
            "faithfulness": None,
            "answer_relevancy": None,
            "context_precision": None,
            "context_recall": None,
        }

    return _run_ragas_evaluate(entries)


def _run_ragas_evaluate(entries: list[EntryResult]) -> dict[str, float | None]:
    """Call ragas.evaluate() on the given entries (live mode)."""
    try:
        import datasets  # noqa: PLC0415
        import ragas  # noqa: PLC0415
        import ragas.metrics  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "ragas and datasets are required for live mode metrics; "
            "install workers[dev] extras"
        ) from exc

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
    eval_result = ragas.evaluate(
        dataset,
        metrics=[
            ragas.metrics.Faithfulness(),
            ragas.metrics.AnswerRelevancy(),
            ragas.metrics.ContextPrecision(),
            ragas.metrics.ContextRecall(),
        ],
    )
    # EvaluationResult.to_pandas() produces a DataFrame with per-metric columns.
    scores = eval_result.to_pandas()  # type: ignore[union-attr]
    return {
        "faithfulness": float(scores["faithfulness"].mean()),
        "answer_relevancy": float(scores["answer_relevancy"].mean()),
        "context_precision": float(scores["context_precision"].mean()),
        "context_recall": float(scores["context_recall"].mean()),
    }
