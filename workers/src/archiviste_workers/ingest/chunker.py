"""RecursiveCharacterTextSplitter sized by the vendored Mixtral-8x7B-v0.1 tokenizer."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from langchain_text_splitters import RecursiveCharacterTextSplitter, TextSplitter
from tokenizers import Tokenizer  # type: ignore[import-untyped]

CHUNK_SIZE: Final = 512
CHUNK_OVERLAP: Final = 64
SEPARATORS: Final = ["\n\n", "\n", ". ", " ", ""]

_ASSET_PATH: Final = Path(__file__).parent / "assets" / "mixtral_8x7b_v0_1_tokenizer.json"


def build_chunker() -> TextSplitter:
    """Build a splitter using the vendored Mixtral-8x7B-v0.1 tokenizer.

    The tokenizer is loaded from a local asset (no network calls).
    Raises RuntimeError if the asset is missing or unreadable.
    """
    if not _ASSET_PATH.exists():
        raise RuntimeError(f"tokenizer artifact not found at {_ASSET_PATH}")
    tokenizer = Tokenizer.from_file(str(_ASSET_PATH))
    return RecursiveCharacterTextSplitter(
        separators=list(SEPARATORS),
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        length_function=lambda text: len(tokenizer.encode(text).ids),
    )
