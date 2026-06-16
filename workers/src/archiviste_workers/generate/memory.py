"""Bounded conversation memory window (MEM-002).

Reads recent turns from the structured ``conversation_messages`` store
(double-written by MEM-001), trims them to a token budget newest-first, and
renders them as alternating Human/AI messages for injection between the system
prompt and the current query. No extra LLM call: the per-turn ``token_count``
persisted by MEM-001 is reused for the budget. Previously-retrieved chunks are
never part of this window — only persisted user/assistant turns are read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from archiviste_workers.conversation.repository import MessageRow

# Upper bound on rows pulled from the tail before budget trimming. A second
# tuning knob beyond the token budget is not justified yet (clean-code).
MEMORY_MAX_TURNS = 50


class TailReader(Protocol):
    """Minimal read surface needed from the conversation store."""

    async def fetch_tail(self, conversation_id: str, *, limit: int) -> list[MessageRow]: ...


@dataclass(frozen=True)
class MemoryWindow:
    """A bounded, chronological view of prior turns plus the last user turn.

    ``messages`` is chronological (oldest-first) and starts on a human turn.
    ``last_user_turn`` is the most recent prior user message, used to make
    elliptical follow-ups self-contained before embedding (intent + retrieval).
    """

    messages: list[BaseMessage]
    last_user_turn: str | None


_EMPTY = MemoryWindow(messages=[], last_user_turn=None)


async def load_memory_window(
    repo: TailReader | None,
    conversation_id: str,
    *,
    token_budget: int,
) -> MemoryWindow:
    """Return prior turns within *token_budget*, newest-first selection rendered chronologically.

    Returns an empty window when no store is wired or the budget is non-positive.
    """
    if repo is None or token_budget <= 0:
        return _EMPTY
    rows = await repo.fetch_tail(conversation_id, limit=MEMORY_MAX_TURNS)
    if not rows:
        return _EMPTY
    return MemoryWindow(
        messages=_render(_select_within_budget(rows, token_budget)),
        last_user_turn=_last_user_turn(rows),
    )


def _select_within_budget(rows_newest_first: list[MessageRow], budget: int) -> list[MessageRow]:
    selected: list[MessageRow] = []
    total = 0
    for row in rows_newest_first:
        total += row.token_count
        if total > budget:
            break
        selected.append(row)
    return selected


def _render(selected_newest_first: list[MessageRow]) -> list[BaseMessage]:
    chronological = list(reversed(selected_newest_first))
    # A window must open on a human turn for clean alternation.
    while chronological and chronological[0].role == "assistant":
        chronological.pop(0)
    return [_to_message(row) for row in chronological]


def _to_message(row: MessageRow) -> BaseMessage:
    if row.role == "assistant":
        return AIMessage(content=row.content)
    return HumanMessage(content=row.content)


def _last_user_turn(rows_newest_first: list[MessageRow]) -> str | None:
    for row in rows_newest_first:
        if row.role == "user":
            return row.content
    return None
