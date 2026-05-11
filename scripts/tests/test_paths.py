"""Unit tests for gdrive_export.paths — AC-7."""

from pathlib import Path

import pytest

from gdrive_export.paths import resolve_local_path

# AC-7: collision resolution, path traversal mitigation, is_relative_to(root).


class TestResolveLocalPath:
    def test_basic_resolution(self, tmp_path: Path) -> None:
        result = resolve_local_path(
            drive_path_components=["Folder"],
            slug="my-doc",
            ext=".md",
            taken_paths=set(),
            drive_file_id="abcdef12",
            root=tmp_path,
        )
        assert result == tmp_path / "folder" / "my-doc.md"

    def test_no_components(self, tmp_path: Path) -> None:
        result = resolve_local_path(
            drive_path_components=[],
            slug="my-doc",
            ext=".md",
            taken_paths=set(),
            drive_file_id="abcdef12",
            root=tmp_path,
        )
        assert result == tmp_path / "my-doc.md"

    def test_nested_components(self, tmp_path: Path) -> None:
        result = resolve_local_path(
            drive_path_components=["Level 1", "Level 2"],
            slug="file",
            ext=".md",
            taken_paths=set(),
            drive_file_id="abcdef12",
            root=tmp_path,
        )
        assert result == tmp_path / "level-1" / "level-2" / "file.md"

    def test_is_relative_to_root(self, tmp_path: Path) -> None:
        result = resolve_local_path(
            drive_path_components=["Sub"],
            slug="doc",
            ext=".md",
            taken_paths=set(),
            drive_file_id="abcdef12",
            root=tmp_path,
        )
        assert result.is_relative_to(tmp_path)

    def test_collision_adds_id_suffix(self, tmp_path: Path) -> None:
        first_path = tmp_path / "doc.md"
        result = resolve_local_path(
            drive_path_components=[],
            slug="doc",
            ext=".md",
            taken_paths={first_path},
            drive_file_id="abcdef12",
            root=tmp_path,
        )
        assert result == tmp_path / "doc-abcdef12.md"

    def test_collision_deterministic(self, tmp_path: Path) -> None:
        # Same inputs always produce same output
        first_path = tmp_path / "report.md"
        result1 = resolve_local_path(
            drive_path_components=[],
            slug="report",
            ext=".md",
            taken_paths={first_path},
            drive_file_id="deadbeef",
            root=tmp_path,
        )
        result2 = resolve_local_path(
            drive_path_components=[],
            slug="report",
            ext=".md",
            taken_paths={first_path},
            drive_file_id="deadbeef",
            root=tmp_path,
        )
        assert result1 == result2

    def test_path_traversal_dotdot_slugified(self, tmp_path: Path) -> None:
        # '..' slugifies to fallback 'file-<id[:8]>', stays under root
        result = resolve_local_path(
            drive_path_components=[".."],
            slug="evil",
            ext=".md",
            taken_paths=set(),
            drive_file_id="abcdef12",
            root=tmp_path,
        )
        assert result.is_relative_to(tmp_path)

    def test_path_traversal_slash_in_component_slugified(self, tmp_path: Path) -> None:
        # A component with '/' gets slugified → safe
        result = resolve_local_path(
            drive_path_components=["../escape"],
            slug="evil",
            ext=".md",
            taken_paths=set(),
            drive_file_id="abcdef12",
            root=tmp_path,
        )
        assert result.is_relative_to(tmp_path)

    def test_two_file_ids_same_slug_distinct_paths(self, tmp_path: Path) -> None:
        # First file gets the plain slug; second (collision) gets suffixed
        slug_path = tmp_path / "report.md"
        result = resolve_local_path(
            drive_path_components=[],
            slug="report",
            ext=".md",
            taken_paths={slug_path},
            drive_file_id="cafebabe",
            root=tmp_path,
        )
        assert result == tmp_path / "report-cafebabe.md"
        assert result != slug_path

    def test_components_slugified(self, tmp_path: Path) -> None:
        result = resolve_local_path(
            drive_path_components=["Mon Dossier Été"],
            slug="mon-fichier",
            ext=".md",
            taken_paths=set(),
            drive_file_id="abcdef12",
            root=tmp_path,
        )
        assert result == tmp_path / "mon-dossier-ete" / "mon-fichier.md"

    def test_collision_suffixed_also_taken_raises(self, tmp_path: Path) -> None:
        # MED-1: when the suffixed candidate is also in taken_paths, the function
        # must not silently return a colliding path — it should raise ValueError.
        plain = tmp_path / "doc.md"
        suffixed = tmp_path / "doc-abcdef12.md"
        with pytest.raises(ValueError, match="collision"):
            resolve_local_path(
                drive_path_components=[],
                slug="doc",
                ext=".md",
                taken_paths={plain, suffixed},
                drive_file_id="abcdef12",
                root=tmp_path,
            )
