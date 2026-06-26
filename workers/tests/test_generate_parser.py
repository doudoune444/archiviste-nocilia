"""GEN-001 citation parser unit tests (AC-12, AC-13)."""

from __future__ import annotations

import structlog
from structlog.testing import capture_logs

from archiviste_workers.generate.models import Chunk
from archiviste_workers.generate.parser import (
    FOLLOWUP_MARKER,
    extract_citations,
    extract_followups,
)


def _setup_structlog() -> None:
    structlog.configure(
        processors=[structlog.testing.LogCapture()],
        wrapper_class=structlog.make_filtering_bound_logger(0),
    )


def test_extract_citations_groups_chunk_ords() -> None:
    # AC-12: same source_path collapses to one citation with all known ords.
    chunks = [
        Chunk(source_path="a/b.md", ord=3, text="t1"),
        Chunk(source_path="a/b.md", ord=5, text="t2"),
        Chunk(source_path="c.md", ord=1, text="t3"),
    ]
    citations = extract_citations("texte [a/b.md] et [c.md]", chunks)
    assert [c.source_path for c in citations] == ["a/b.md", "c.md"]
    assert citations[0].chunk_ords == [3, 5]
    assert citations[1].chunk_ords == [1]


def test_extract_citations_filters_unknown_source() -> None:
    # AC-12: unknown source_path -> filtered + log warn.
    chunks = [Chunk(source_path="a/b.md", ord=1, text="t1")]
    with capture_logs() as captured:
        citations = extract_citations("citation [d.md] et [a/b.md]", chunks)
    assert [c.source_path for c in citations] == ["a/b.md"]
    assert any(
        log.get("event") == "citation_unknown_source" and log.get("source_path") == "d.md"
        for log in captured
    )


def test_extract_citations_no_brackets() -> None:
    # AC-13: answer without brackets -> empty list (caller logs llm_no_citation).
    chunks = [Chunk(source_path="a/b.md", ord=1, text="t1")]
    assert extract_citations("plain answer no citations", chunks) == []


def test_extract_citations_single_path() -> None:
    # #345: a lone known path in one bracket stays a single citation.
    chunks = [Chunk(source_path="a/b.md", ord=2, text="t1")]
    citations = extract_citations("fait [a/b.md]", chunks)
    assert [c.source_path for c in citations] == ["a/b.md"]
    assert citations[0].chunk_ords == [2]


def test_extract_citations_comma_grouped_bracket() -> None:
    # #345 BUG B: model groups several sources in ONE bracket `[a, b]`; the
    # defensive split matches each comma-separated piece against known paths.
    chunks = [
        Chunk(source_path="lore/a.md", ord=1, text="t1"),
        Chunk(source_path="lore/b.md", ord=2, text="t2"),
    ]
    citations = extract_citations("affirmation [lore/a.md, lore/b.md]", chunks)
    assert [c.source_path for c in citations] == ["lore/a.md", "lore/b.md"]
    assert citations[0].chunk_ords == [1]
    assert citations[1].chunk_ords == [2]


def test_extract_citations_accolated_brackets() -> None:
    # #345: the canon form `[a][b]` already matches per-bracket — still two citations.
    chunks = [
        Chunk(source_path="lore/a.md", ord=1, text="t1"),
        Chunk(source_path="lore/b.md", ord=2, text="t2"),
    ]
    citations = extract_citations("affirmation [lore/a.md][lore/b.md]", chunks)
    assert [c.source_path for c in citations] == ["lore/a.md", "lore/b.md"]


def test_extract_citations_comma_partial_known() -> None:
    # #345: a grouped bracket with one unknown piece keeps only the known one + warns.
    chunks = [Chunk(source_path="lore/a.md", ord=1, text="t1")]
    with capture_logs() as captured:
        citations = extract_citations("affirmation [lore/a.md, lore/inconnu.md]", chunks)
    assert [c.source_path for c in citations] == ["lore/a.md"]
    assert any(
        log.get("event") == "citation_unknown_source"
        and log.get("source_path") == "lore/inconnu.md"
        for log in captured
    )


# #354 — extract_followups: mirror of extract_citations for the sentinel block.


def test_extract_followups_two_questions() -> None:
    # #354 AC: 2 questions extracted, sentinel block removed from the body.
    answer = (
        "L'Archiviste consulte ses parchemins. [a/b.md]\n"
        f"{FOLLOWUP_MARKER}\n"
        "- Qui a fondé Nocilia ?\n"
        "- Quand l'Archiviste est-il apparu ?"
    )
    body, followups = extract_followups(answer)
    assert body == "L'Archiviste consulte ses parchemins. [a/b.md]"
    assert followups == ["Qui a fondé Nocilia ?", "Quand l'Archiviste est-il apparu ?"]
    assert FOLLOWUP_MARKER not in body


def test_extract_followups_one_question() -> None:
    # #354 AC: a single follow-up line is still extracted.
    answer = f"Corps de réponse.\n{FOLLOWUP_MARKER}\n- Une seule question ?"
    body, followups = extract_followups(answer)
    assert body == "Corps de réponse."
    assert followups == ["Une seule question ?"]


def test_extract_followups_zero_questions() -> None:
    # #354 AC: marker present but no follow-up lines -> empty list, body stripped.
    answer = f"Corps de réponse.\n{FOLLOWUP_MARKER}\n"
    body, followups = extract_followups(answer)
    assert body == "Corps de réponse."
    assert followups == []


def test_extract_followups_marker_absent_returns_body_unchanged() -> None:
    # #354 AC: no marker -> body unchanged byte-for-byte, empty list.
    answer = "Corps de réponse sans suivi. [a/b.md]"
    body, followups = extract_followups(answer)
    assert body == answer
    assert followups == []


# #345 BUG A — tolerant marker matching: mistral-small emits variants of the
# sentinel. The marker is the FIRST whole line that reduces to the SUIVI token.


def test_extract_followups_tolerant_canonical() -> None:
    # #345: the canonical `---SUIVI---` line is recognised (regression baseline).
    answer = "Corps.\n---SUIVI---\n- Q1 ?\n- Q2 ?"
    body, followups = extract_followups(answer)
    assert body == "Corps."
    assert followups == ["Q1 ?", "Q2 ?"]


def test_extract_followups_tolerant_no_leading_dashes() -> None:
    # #345: `SUIVI---` (model dropped the leading dashes) still cuts.
    answer = "Corps.\nSUIVI---\n- Q1 ?"
    body, followups = extract_followups(answer)
    assert body == "Corps."
    assert followups == ["Q1 ?"]


def test_extract_followups_tolerant_dashes_on_own_line() -> None:
    # #345: `---` on its own line then `SUIVI---` — the dangling rule is stripped.
    answer = "Corps.\n---\nSUIVI---\n- Q1 ?"
    body, followups = extract_followups(answer)
    assert body == "Corps."
    assert "---" not in body
    assert followups == ["Q1 ?"]


def test_extract_followups_tolerant_spaces() -> None:
    # #345: `--- SUIVI ---` with spaces around the token still cuts.
    answer = "Corps.\n--- SUIVI ---\n- Q1 ?"
    body, followups = extract_followups(answer)
    assert body == "Corps."
    assert followups == ["Q1 ?"]


def test_extract_followups_tolerant_indented_questions() -> None:
    # #345: 4-space-indented questions are de-indented and de-bulleted.
    answer = "Corps.\n---SUIVI---\n    - Q1 ?\n    - Q2 ?"
    body, followups = extract_followups(answer)
    assert body == "Corps."
    assert followups == ["Q1 ?", "Q2 ?"]


def test_extract_followups_does_not_trigger_on_word_suivi_midline() -> None:
    # #345 CRITICAL: the French word "suivi"/"Suivi" mid-sentence must NEVER be
    # mistaken for the whole-line marker — no body cut, no false follow-ups.
    answer = (
        "L'Archiviste assure le suivi des âmes. [a/b.md]\nSuivi attentivement, ce rite perdure."
    )
    body, followups = extract_followups(answer)
    assert body == answer
    assert followups == []
