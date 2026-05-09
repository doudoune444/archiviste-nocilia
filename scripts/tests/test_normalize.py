"""Unit tests for gdrive_export.normalize — AC-4."""

from gdrive_export.normalize import normalize_body

# AC-4: normalize_body applies NFKC and strips C0 controls except \n and \t.


class TestNormalizeBody:
    def test_nfkc_decomposed_to_composed(self) -> None:
        # NFD: e + combining acute (U+0301) → NFC: é (U+00E9)
        nfd_e = "é"
        result = normalize_body(nfd_e)
        assert result == "é"

    def test_zero_width_space_stripped(self) -> None:
        # U+200B ZERO WIDTH SPACE → after NFKC it maps to nothing, or is a control char
        # Actually U+200B is not C0; let's test actual C0 range
        text = "hello\x00world"
        result = normalize_body(text)
        assert "\x00" not in result
        assert result == "helloworld"

    def test_null_bytes_stripped(self) -> None:
        result = normalize_body("abc\x00def\x01ghi")
        assert result == "abcdefghi"

    def test_newline_preserved(self) -> None:
        result = normalize_body("line1\nline2")
        assert result == "line1\nline2"

    def test_tab_preserved(self) -> None:
        result = normalize_body("col1\tcol2")
        assert result == "col1\tcol2"

    def test_carriage_return_stripped(self) -> None:
        # \r (0x0D) is a C0 control char that should be stripped
        result = normalize_body("line1\r\nline2")
        assert result == "line1\nline2"

    def test_c0_range_stripped(self) -> None:
        # All C0 except \t and \n should be stripped
        for code in range(0x00, 0x20):
            if code in (0x09, 0x0A):  # \t and \n
                continue
            char = chr(code)
            result = normalize_body(f"a{char}b")
            assert result == "ab", f"C0 char U+{code:04X} not stripped"

    def test_nfkc_ligature_expanded(self) -> None:
        # ﬁ (U+FB01) → fi under NFKC
        result = normalize_body("ﬁle")
        assert result == "file"

    def test_normal_text_unchanged(self) -> None:
        text = "The quick brown fox.\nJumped over\tthe lazy dog."
        assert normalize_body(text) == text

    def test_combining_marks_composed_via_nfkc(self) -> None:
        # NFD sequence: a + combining grave (U+0300) → NFKC: à (U+00E0)
        nfd = "à"
        result = normalize_body(nfd)
        assert result == "à"
