"""Tests for agents.py parse_proposal function."""

from __future__ import annotations

import pytest

from agents import parse_proposal


class TestParseProposal:
    """Tests for the parse_proposal function."""

    def test_valid_json_proposal(self) -> None:
        """A valid JSON proposal should be parsed correctly."""
        raw = """{"summary": "Create hello.py", "files": [{"path": "hello.py", "content": "print('hello')"}] }"""
        proposal = parse_proposal(raw)
        assert proposal["summary"] == "Create hello.py"
        assert len(proposal["files"]) == 1
        assert proposal["files"][0]["path"] == "hello.py"
        assert proposal["files"][0]["content"] == "print('hello')"

    def test_proposal_with_markdown_fences(self) -> None:
        """Markdown code fences should be stripped before parsing."""
        raw = '''```json
{"summary": "Test", "files": [{"path": "test.py", "content": "x=1"}]}
```'''
        proposal = parse_proposal(raw)
        assert proposal["summary"] == "Test"
        assert proposal["files"][0]["path"] == "test.py"

    def test_proposal_with_extra_text(self) -> None:
        """Extra text around JSON should be handled (extract from first { to last })."""
        raw = 'Some text before {"summary": "Test", "files": [{"path": "a.py", "content": "1"}]} some text after'
        proposal = parse_proposal(raw)
        assert proposal["summary"] == "Test"

    def test_missing_files_list_raises(self) -> None:
        """A proposal missing a files list should raise ValueError."""
        raw = '{"summary": "No files"}'
        with pytest.raises(ValueError, match="missing a files list"):
            parse_proposal(raw)

    def test_empty_files_list_is_valid(self) -> None:
        """An empty files list is valid (empty list is still a list)."""
        raw = '{"summary": "Empty", "files": []}'
        proposal = parse_proposal(raw)
        assert proposal["summary"] == "Empty"
        assert proposal["files"] == []

    def test_absolute_path_rejected(self) -> None:
        """Absolute paths should be rejected."""
        raw = '{"summary": "Bad", "files": [{"path": "C:/", "content": "bad"}]}'
        with pytest.raises(ValueError, match="Unsafe proposed path"):
            parse_proposal(raw)

    def test_path_traversal_rejected(self) -> None:
        """Path traversal with .. should be rejected."""
        raw = '{"summary": "Bad", "files": [{"path": "../../etc/passwd", "content": "bad"}]}'
        with pytest.raises(ValueError, match="Unsafe proposed path"):
            parse_proposal(raw)

    def test_no_json_braces_raises(self) -> None:
        """Input without JSON braces should raise ValueError."""
        raw = "This is just plain text with no JSON"
        with pytest.raises(ValueError, match="did not return a JSON proposal"):
            parse_proposal(raw)

    def test_empty_string_raises(self) -> None:
        """Empty string should raise ValueError."""
        raw = ""
        with pytest.raises(ValueError):
            parse_proposal(raw)

    def test_multiple_files(self) -> None:
        """A proposal with multiple files should parse all of them."""
        raw = (
            '{"summary": "Multiple files", '
            '"files": ['
            '{"path": "a.py", "content": "a=1"}, '
            '{"path": "b.py", "content": "b=2"}'
            "]}"
        )
        proposal = parse_proposal(raw)
        assert len(proposal["files"]) == 2
        assert proposal["files"][0]["path"] == "a.py"
        assert proposal["files"][1]["path"] == "b.py"

    def test_nested_directory_path(self) -> None:
        """Relative paths with subdirectories should be accepted."""
        raw = '{"summary": "Nested", "files": [{"path": "src/main.py", "content": "x=1"}]}'
        proposal = parse_proposal(raw)
        assert proposal["files"][0]["path"] == "src/main.py"
