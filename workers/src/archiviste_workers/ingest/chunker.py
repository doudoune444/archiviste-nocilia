"""RecursiveCharacterTextSplitter wired to the bge-m3 tokenizer."""

from __future__ import annotations

from typing import Final

from langchain_text_splitters import RecursiveCharacterTextSplitter, TextSplitter
from transformers import AutoTokenizer

CHUNK_SIZE: Final = 512
CHUNK_OVERLAP: Final = 64
SEPARATORS: Final = ["\n\n", "\n", ". ", " ", ""]
TOKENIZER_NAME: Final = "BAAI/bge-m3"


def build_chunker() -> TextSplitter:
    """Build a splitter using the bge-m3 tokenizer with the project's fixed parameters."""
    tokenizer = AutoTokenizer.from_pretrained(TOKENIZER_NAME)
    return RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
        tokenizer,
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=list(SEPARATORS),
    )
