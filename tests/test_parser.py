"""Tests for markdown parser."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from notedrill.parser import (
    extract_wikilinks,
    extract_tags,
    extract_code_blocks,
    _split_sections,
)


class TestExtractWikilinks:
    def test_simple_wikilink(self):
        links = extract_wikilinks("See [[Python]] for details.")
        assert links == ["Python"]

    def test_multiple_wikilinks(self):
        links = extract_wikilinks("[[Python]] and [[Rust]] are great.")
        assert links == ["Python", "Rust"]

    def test_wikilink_with_alias(self):
        links = extract_wikilinks("See [[Python|the best language]]!")
        assert links == ["Python"]

    def test_wikilink_with_header(self):
        links = extract_wikilinks("See [[Python#History]] for more.")
        assert links == ["Python"]

    def test_no_wikilinks(self):
        links = extract_wikilinks("Just plain text.")
        assert links == []


class TestExtractTags:
    def test_simple_tag(self):
        tags = extract_tags("I love #python")
        assert "python" in tags

    def test_tag_with_slash(self):
        tags = extract_tags("Using #lang/rust here")
        assert "lang/rust" in tags

    def test_multiple_tags(self):
        tags = extract_tags("#python #rust #golang")
        assert len(tags) == 3

    def test_no_tags(self):
        tags = extract_tags("Plain text without tags")
        assert tags == []

    def test_duplicate_tags(self):
        tags = extract_tags("#python and #python again")
        assert tags == ["python"]

    def test_cjk_tags(self):
        tags = extract_tags("学习 #编程 和 #算法")
        assert len(tags) >= 1


class TestExtractCodeBlocks:
    def test_python_block(self):
        text = "```python\nprint('hello')\n```"
        blocks = extract_code_blocks(text)
        assert blocks == ["print('hello')"]

    def test_no_language_block(self):
        text = "```\nplain code\n```"
        blocks = extract_code_blocks(text)
        assert blocks == ["plain code"]

    def test_multiple_blocks(self):
        text = "```python\na=1\n```\n\n```rust\nlet x=2;\n```"
        blocks = extract_code_blocks(text)
        assert len(blocks) == 2
        assert "a=1" in blocks[0]
        assert "let x=2" in blocks[1]


class TestSplitSections:
    def test_single_heading(self):
        text = "# Title\n\nContent here."
        sections = _split_sections(text)
        # There should be at least one section with heading "Title"
        titles = [s.heading for s in sections]
        assert "Title" in titles

    def test_multiple_headings(self):
        text = "# H1\n\nintro\n\n## H2\n\ndetails\n\n### H3\n\nmore"
        sections = _split_sections(text)
        headings = [s.heading for s in sections]
        assert "H1" in headings
        assert "H2" in headings
        assert "H3" in headings

    def test_sections_have_ids(self):
        text = "# Title\n\nContent."
        sections = _split_sections(text)
        for s in sections:
            assert s.id, f"Section '{s.heading}' has no id"

    def test_section_levels(self):
        text = "# H1\ncontent\n## H2\ncontent\n### H3\ncontent"
        sections = _split_sections(text)
        levels = {s.heading: s.level for s in sections if s.heading}
        assert levels.get("H1") == 1
        assert levels.get("H2") == 2
        assert levels.get("H3") == 3

    def test_code_blocks_in_sections(self):
        text = "# Title\n\nHere is code:\n```python\nx=1\n```\n\ntext"
        sections = _split_sections(text)
        for s in sections:
            if s.heading == "Title":
                assert len(s.code_blocks) >= 1
                assert "x=1" in s.code_blocks[0]

    def test_empty_content(self):
        sections = _split_sections("")
        assert len(sections) == 1  # One empty section

    def test_no_headings(self):
        sections = _split_sections("Just some text without any headings.")
        assert len(sections) >= 1
