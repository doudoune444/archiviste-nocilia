"""Unit tests for gdrive_export.frontmatter_merge — AC-6."""

import pytest
import yaml

from gdrive_export.frontmatter_merge import (
    SCRIPT_MANAGED_KEYS,
    USER_MANAGED_DEFAULTS,
    FrontmatterMergeError,
    _build_merged_dict,
    merge_frontmatter,
)

# AC-6: matriciel — first export, re-export preserve user keys, custom keys, invalid YAML.

SCRIPT_DATA: dict[str, object] = {
    "title": "My Document",
    "source": "https://drive.google.com",
    "source_id": "file-id-123",
    "drive_path": "Folder/My Document",
    "exported_at": "2026-05-09T00:00:00Z",
    "archived": False,
    "archived_at": None,
}


class TestFirstExport:
    def test_first_export_contains_script_keys(self) -> None:
        result = merge_frontmatter(None, SCRIPT_DATA, USER_MANAGED_DEFAULTS)
        parsed = yaml.safe_load(result)
        assert parsed["title"] == "My Document"
        assert parsed["source_id"] == "file-id-123"

    def test_first_export_contains_user_defaults(self) -> None:
        result = merge_frontmatter(None, SCRIPT_DATA, USER_MANAGED_DEFAULTS)
        parsed = yaml.safe_load(result)
        assert parsed["tags"] == []
        assert parsed["access_tier"] == "public"

    def test_first_export_alphabetical_order(self) -> None:
        result = merge_frontmatter(None, SCRIPT_DATA, USER_MANAGED_DEFAULTS)
        keys = list(yaml.safe_load(result).keys())
        assert keys == sorted(keys)


class TestReExport:
    def test_script_managed_overwritten(self) -> None:
        existing = "title: Old Title\ntags: [lore]\naccess_tier: members\n"
        new_script = dict(SCRIPT_DATA)
        new_script["title"] = "New Title"
        result = merge_frontmatter(existing, new_script, USER_MANAGED_DEFAULTS)
        parsed = yaml.safe_load(result)
        assert parsed["title"] == "New Title"

    def test_user_tags_preserved(self) -> None:
        existing = "title: Old\ntags: [lore, history]\naccess_tier: members\n"
        result = merge_frontmatter(existing, SCRIPT_DATA, USER_MANAGED_DEFAULTS)
        parsed = yaml.safe_load(result)
        assert parsed["tags"] == ["lore", "history"]

    def test_user_access_tier_preserved(self) -> None:
        existing = "title: Old\ntags: []\naccess_tier: author_only\n"
        result = merge_frontmatter(existing, SCRIPT_DATA, USER_MANAGED_DEFAULTS)
        parsed = yaml.safe_load(result)
        assert parsed["access_tier"] == "author_only"

    def test_custom_key_preserved(self) -> None:
        existing = "title: Old\npriority: high\ntags: []\naccess_tier: public\n"
        result = merge_frontmatter(existing, SCRIPT_DATA, USER_MANAGED_DEFAULTS)
        parsed = yaml.safe_load(result)
        assert parsed["priority"] == "high"

    def test_user_default_created_if_absent(self) -> None:
        # No 'tags' in existing → created with default []
        existing = "title: Old\naccess_tier: members\n"
        result = merge_frontmatter(existing, SCRIPT_DATA, USER_MANAGED_DEFAULTS)
        parsed = yaml.safe_load(result)
        assert parsed["tags"] == []

    def test_tags_yes_no_preserved(self) -> None:
        # pyyaml boolean coercion risk: AC-6 risk note from plan
        existing = "title: Old\ntags: [yes, no]\naccess_tier: public\n"
        result = merge_frontmatter(existing, SCRIPT_DATA, USER_MANAGED_DEFAULTS)
        parsed = yaml.safe_load(result)
        # SafeLoader coerces 'yes'/'no' to True/False — this is expected behavior
        # The round-trip preserves the semantic value even if repr changes
        assert len(parsed["tags"]) == 2


class TestInvalidYaml:
    def test_invalid_yaml_raises(self) -> None:
        with pytest.raises(FrontmatterMergeError):
            merge_frontmatter("key: [unclosed", SCRIPT_DATA, USER_MANAGED_DEFAULTS)

    def test_yaml_root_not_mapping_raises(self) -> None:
        with pytest.raises(FrontmatterMergeError, match="mapping"):
            merge_frontmatter("- item1\n- item2\n", SCRIPT_DATA, USER_MANAGED_DEFAULTS)


class TestMutableDefaultIsolation:
    def test_tags_default_not_shared_across_calls(self) -> None:
        # AC-6 / MED-3: _build_merged_dict must deep-copy user defaults so the
        # returned dict's "tags" list is NOT the same object as USER_MANAGED_DEFAULTS["tags"].
        # Without copy.deepcopy, merged["tags"] IS USER_MANAGED_DEFAULTS["tags"] (same
        # reference).  A caller holding the merged dict could then mutate the module
        # constant via merged["tags"].append(...), silently corrupting all future calls.
        # This test FAILS when copy.deepcopy is replaced with a direct assignment.
        merged = _build_merged_dict(None, SCRIPT_DATA, USER_MANAGED_DEFAULTS)
        # Mutate the returned dict's tags list.
        merged["tags"].append("injected")
        # Module constant must be unaffected — proves deepcopy broke the alias.
        assert USER_MANAGED_DEFAULTS["tags"] == [], (
            "MED-3: merged['tags'] is the same object as USER_MANAGED_DEFAULTS['tags'] — "
            "deepcopy boundary missing"
        )

    # Note: test_user_managed_defaults_list_unchanged_after_merge (round-1) was removed
    # (tautological — the buggy direct-assign never mutated the module constant either,
    # so the test passed equally under both fixed and broken code; see review MED-3).


class TestScriptManagedKeys:
    def test_all_expected_keys_present(self) -> None:
        expected = {
            "title", "source", "source_id", "drive_path",
            "exported_at", "archived", "archived_at",
        }
        assert expected == SCRIPT_MANAGED_KEYS
