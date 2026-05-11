"""Unit tests for gdrive_export.state — AC-5."""

import json
from pathlib import Path

import pytest

from gdrive_export.state import (
    StateCorruptedError,
    StateEntry,
    compute_body_hash,
    load_state,
    save_state,
)

# AC-5: round-trip, corrupted, absent, compute_body_hash deterministic.


def make_entry(local_path: str = "lore/doc.md") -> StateEntry:
    return StateEntry(
        local_path=local_path,
        drive_md5_checksum="d41d8cd98f00b204e9800998ecf8427e",
        last_exported_at="2026-05-09T00:00:00Z",
        body_hash="abc123",
        archived_at=None,
    )


class TestLoadState:
    def test_absent_file_returns_empty_dict(self, tmp_path: Path) -> None:
        result = load_state(tmp_path / "nonexistent.json")
        assert result == {}

    def test_invalid_json_raises_corrupted(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        state_file.write_text("not json!", encoding="utf-8")
        with pytest.raises(StateCorruptedError, match="not valid JSON"):
            load_state(state_file)

    def test_root_not_dict_raises_corrupted(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        state_file.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(StateCorruptedError, match="must be a JSON object"):
            load_state(state_file)

    def test_entry_not_dict_raises_corrupted(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        state_file.write_text('{"id1": "not-an-object"}', encoding="utf-8")
        with pytest.raises(StateCorruptedError, match="must be a JSON object"):
            load_state(state_file)

    def test_entry_missing_field_raises_corrupted(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        state_file.write_text(
            '{"id1": {"local_path": "x"}}', encoding="utf-8"
        )
        with pytest.raises(StateCorruptedError, match="missing field"):
            load_state(state_file)


class TestSaveState:
    def test_save_creates_file(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        save_state(state_file, {})
        assert state_file.exists()

    def test_save_produces_sorted_keys(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        state = {"zzz": make_entry("lore/z.md"), "aaa": make_entry("lore/a.md")}
        save_state(state_file, state)
        content = state_file.read_text(encoding="utf-8")
        parsed = json.loads(content)
        assert list(parsed.keys()) == sorted(parsed.keys())

    def test_save_indent_2(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        save_state(state_file, {"x": make_entry()})
        content = state_file.read_text(encoding="utf-8")
        assert '  "' in content  # indented


class TestRoundTrip:
    def test_round_trip(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        original = {
            "file-id-1": make_entry("lore/doc.md"),
            "file-id-2": StateEntry(
                local_path="lore/other.md",
                drive_md5_checksum="checksum2",
                last_exported_at="2026-01-01T00:00:00Z",
                body_hash="deadbeef",
                archived_at="2026-03-01T00:00:00Z",
            ),
        }
        save_state(state_file, original)
        loaded = load_state(state_file)
        assert loaded == original

    def test_round_trip_archived_at_none(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        entry = make_entry()
        assert entry.archived_at is None
        save_state(state_file, {"x": entry})
        loaded = load_state(state_file)
        assert loaded["x"].archived_at is None


class TestAtomicSave:
    def test_original_intact_when_replace_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # AC-5 / MED-2: atomicity — if os.replace raises mid-write the original state
        # file must be left unchanged.  Pre-fix (write_text directly) would have already
        # overwritten the original before any error could occur, so this test FAILS
        # without the tmp-then-replace implementation.
        state_file = tmp_path / "state.json"
        original_state = {"original": make_entry("lore/original.md")}
        save_state(state_file, original_state)
        original_content = state_file.read_text(encoding="utf-8")

        def _raise(*_args: object, **_kwargs: object) -> None:
            raise OSError("simulated crash during atomic replace")

        monkeypatch.setattr("gdrive_export.state.os.replace", _raise)

        new_state = {"new": make_entry("lore/new.md")}
        with pytest.raises(OSError, match="simulated crash"):
            save_state(state_file, new_state)

        # Original must be byte-for-byte intact — not partially overwritten.
        assert state_file.read_text(encoding="utf-8") == original_content, (
            "MED-2: original state file was corrupted before os.replace — not atomic"
        )


class TestComputeBodyHash:
    def test_deterministic(self) -> None:
        assert compute_body_hash("hello") == compute_body_hash("hello")

    def test_different_inputs_different_hashes(self) -> None:
        assert compute_body_hash("hello") != compute_body_hash("world")

    def test_sha256_hex_length(self) -> None:
        result = compute_body_hash("test")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_empty_string(self) -> None:
        # sha256("") is known
        result = compute_body_hash("")
        assert result == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
