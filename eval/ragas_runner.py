"""RAG quality eval runner — Ragas vs golden Q/A set.

Skeleton. The eval-runner agent + first eval ticket will fill the body.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EvalResult:
    faithfulness: float
    answer_relevancy: float
    context_precision: float
    context_recall: float
    samples: list[dict]


def load_golden_set(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_baseline(path: Path) -> dict[str, float] | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def compare_to_baseline(current: EvalResult, baseline: dict[str, float] | None) -> str:
    if baseline is None:
        return "PASS (no baseline yet)"
    thresholds = {
        "faithfulness": 0.02,
        "answer_relevancy": 0.02,
        "context_precision": 0.03,
        "context_recall": 0.03,
    }
    for metric, max_drop in thresholds.items():
        delta = getattr(current, metric) - baseline.get(metric, 0)
        if delta < -max_drop:
            return f"BLOCK: {metric} dropped {delta:.3f} (max allowed -{max_drop})"
    return "PASS"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", required=True, type=Path)
    parser.add_argument("--baseline", default=Path("eval/baseline.json"), type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers-url", default="http://localhost:8000")
    parser.add_argument("--quick", action="store_true", help="run subset of golden set")
    args = parser.parse_args()

    golden = load_golden_set(args.set)
    if args.quick:
        golden = golden[:5]

    # TODO: actually run retrieval + generation against workers, then call ragas.
    # Stub returning constants for now.
    result = EvalResult(
        faithfulness=0.0,
        answer_relevancy=0.0,
        context_precision=0.0,
        context_recall=0.0,
        samples=[],
    )

    baseline = load_baseline(args.baseline)
    verdict = compare_to_baseline(result, baseline)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "metrics": result.__dict__,
                "verdict": verdict,
                "n_samples": len(golden),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Verdict: {verdict}")
    return 0 if verdict.startswith("PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
