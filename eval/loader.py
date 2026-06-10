"""Golden set loader with strict Pydantic schema validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

VALID_MODES = frozenset({"canon", "off_topic", "lore_gap", "mystery"})


class GoldenEntry(BaseModel):
    """Single golden Q/A entry. Extra fields forbidden (AC-1)."""

    model_config = {"extra": "forbid"}

    id: str = Field(min_length=1)
    mode: Literal["canon", "off_topic", "lore_gap", "mystery"]
    question: str = Field(min_length=1)
    expected_contexts: list[str]
    expected_answer_keywords: list[str]
    difficulty: str | None = None
    category: str | None = None

    @field_validator("expected_contexts", mode="before")
    @classmethod
    def validate_contexts_is_list(cls, value: object) -> object:
        if not isinstance(value, list):
            raise ValueError("expected_contexts must be a list")
        return value

    @field_validator("expected_answer_keywords", mode="before")
    @classmethod
    def validate_keywords_is_list(cls, value: object) -> object:
        if not isinstance(value, list):
            raise ValueError("expected_answer_keywords must be a list")
        return value

    @model_validator(mode="before")
    @classmethod
    def validate_id_present(cls, data: object) -> object:
        if isinstance(data, dict) and not data.get("id"):
            raise ValueError("field 'id' is required and must be non-empty")
        return data


def load_golden_set(path: Path) -> list[GoldenEntry]:
    """Load and validate all entries from a JSONL golden set file.

    Raises ValueError citing id + field if any entry is invalid (AC-1).
    No metrics are computed until all entries pass validation.
    """
    raw_entries: list[tuple[int, dict[str, object]]] = []
    with path.open(encoding="utf-8") as fh:
        for line_num, raw_line in enumerate(fh, start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {line_num}: invalid JSON — {exc}") from exc
            raw_entries.append((line_num, data))

    validated: list[GoldenEntry] = []
    for line_num, data in raw_entries:
        if isinstance(data, dict):
            entry_id = data.get("id", f"<line {line_num}>")
        else:
            entry_id = f"<line {line_num}>"
        try:
            validated.append(GoldenEntry.model_validate(data))
        except Exception as exc:
            raise ValueError(f"entry id={entry_id!r}: {exc}") from exc

    return validated
