"""Turn token counter for conversation_messages (MEM-001).

Uses the vendored Mixtral-8x7B-v0.1 tokenizer — the same artifact already
used by the chunker (ING-016). Loaded once at module import; subsequent calls
are in-process and network-free.

Tokenizer choice is intentionally consistent with the chunker so that
token_count values are comparable with CHUNK_SIZE (512 tok). A more precise
per-provider counter (tiktoken for OpenAI, Anthropic counting API, …) is
deferred to MEM-002 when the budget-windowing AC demands it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from tokenizers import Tokenizer  # type: ignore[import-untyped]

_ASSET_PATH: Final = (
    Path(__file__).parent.parent / "ingest" / "assets" / "mixtral_8x7b_v0_1_tokenizer.json"
)


def _load_tokenizer() -> Tokenizer:
    if not _ASSET_PATH.exists():
        raise RuntimeError(f"tokenizer artifact not found at {_ASSET_PATH}")
    return Tokenizer.from_file(str(_ASSET_PATH))


_TOKENIZER: Tokenizer = _load_tokenizer()


def count_tokens(text: str) -> int:
    """Return the number of Mixtral-8x7B-v0.1 tokens in *text*."""
    return len(_TOKENIZER.encode(text).ids)
