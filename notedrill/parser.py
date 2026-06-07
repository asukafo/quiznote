"""Obsidian-flavoured Markdown parser.

Parses a vault directory of .md files into structured Note objects,
and builds directory trees for the web UI.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import frontmatter

from .models import Note, Section, new_id

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[^\]])*\]\]")
TAG_RE = re.compile(r"(?:^|\s)#([a-zA-Z一-鿿][\w一-鿿/-]*)")
CODE_BLOCK_RE = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)

SKIP_DIRS = {".obsidian", ".git", ".trash", ".github", "images", "assets", ".quiznote"}


def extract_wikilinks(text: str) -> list[str]:
    return [m.group(1).strip() for m in WIKILINK_RE.finditer(text)]


def extract_tags(text: str) -> list[str]:
    return list(set(m.group(1) for m in TAG_RE.finditer(text)))


def extract_code_blocks(text: str) -> list[str]:
    return [m.group(2).strip() for m in CODE_BLOCK_RE.finditer(text)]


# ---------------------------------------------------------------------------
# Markdown → sections
# ---------------------------------------------------------------------------

def _split_sections(md_text: str) -> list[Section]:
    lines = md_text.split("\n")
    sections: list[Section] = []
    current_heading = ""
    current_level = 1
    current_lines: list[str] = []

    heading_re = re.compile(r"^(#{1,6})\s+(.+)")

    for line in lines:
        m = heading_re.match(line)
        if m:
            if current_lines or current_heading:
                body = "\n".join(current_lines).strip()
                sections.append(
                    Section(
                        id=new_id(),
                        heading=current_heading,
                        level=current_level,
                        content=body,
                        code_blocks=extract_code_blocks(body),
                    )
                )
            current_heading = m.group(2).strip()
            current_level = len(m.group(1))
            current_lines = []
        else:
            current_lines.append(line)

    body = "\n".join(current_lines).strip()
    sections.append(
        Section(
            id=new_id(),
            heading=current_heading,
            level=current_level,
            content=body,
            code_blocks=extract_code_blocks(body),
        )
    )
    return sections


# ---------------------------------------------------------------------------
# Read frontmatter title without full parse
# ---------------------------------------------------------------------------

def _read_title(file_path: str) -> str:
    """Quickly read the title from frontmatter or first h1."""
    try:
        with open(file_path, encoding="utf-8") as f:
            post = frontmatter.load(f)
        fm_title = (post.metadata or {}).get("title", "")
        if fm_title:
            return str(fm_title)
        content = post.content or ""
        h1_match = re.search(r"^#\s+(.+)", content, re.MULTILINE)
        if h1_match:
            return h1_match.group(1).strip()
    except Exception:
        pass
    return Path(file_path).stem.replace("_", " ").replace("-", " ")


# ---------------------------------------------------------------------------
# Directory tree for web UI
# ---------------------------------------------------------------------------

def list_vault_tree(vault_path: str) -> dict:
    """Build a directory tree of all markdown files in the vault.

    Returns a nested dict:
    {
        "name": "root",
        "path": "root",
        "type": "directory",
        "children": [
            {"name": "subdir", "type": "directory", "children": [
                {"name": "note.md", "type": "file", "path": "subdir/note.md", "title": "My Note"}
            ]}
        ]
    }
    """
    root = Path(vault_path)

    def _walk(dir_path: Path, rel: str) -> dict:
        node: dict = {
            "name": dir_path.name if dir_path != root else os.path.basename(vault_path),
            "path": rel,
            "type": "directory",
            "children": [],
        }

        try:
            entries = sorted(dir_path.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        except PermissionError:
            return node

        for entry in entries:
            # Skip hidden files/dirs
            if entry.name.startswith("."):
                continue
            # Skip known non-content dirs
            if entry.is_dir() and entry.name in SKIP_DIRS:
                continue
            # Skip non-md files
            if entry.is_file() and entry.suffix != ".md":
                continue

            child_rel = f"{rel}/{entry.name}" if rel else entry.name

            if entry.is_dir():
                child = _walk(entry, child_rel)
            else:
                child = {
                    "name": entry.name,
                    "path": child_rel,
                    "type": "file",
                    "title": _read_title(str(entry)),
                }
            node["children"].append(child)

        return node

    tree = _walk(root, "")
    # Add a virtual "all" count at root
    return tree


def count_md_files(tree: dict) -> int:
    """Count all markdown files in a tree."""
    count = 0
    for child in tree.get("children", []):
        if child["type"] == "file":
            count += 1
        elif child["type"] == "directory":
            count += count_md_files(child)
    return count


def collect_file_paths(tree: dict) -> list[str]:
    """Collect all file paths from a tree node."""
    paths = []
    for child in tree.get("children", []):
        if child["type"] == "file":
            paths.append(child["path"])
        elif child["type"] == "directory":
            paths.extend(collect_file_paths(child))
    return paths


# ---------------------------------------------------------------------------
# Parse vault
# ---------------------------------------------------------------------------

def parse_vault(vault_path: str) -> list[Note]:
    """Parse all .md files in a vault directory into Note objects."""
    vault = Path(vault_path)
    if not vault.exists():
        raise FileNotFoundError(f"Vault directory not found: {vault_path}")

    notes: list[Note] = []
    for md_file in sorted(vault.rglob("*.md")):
        if _should_skip(md_file, vault):
            continue
        try:
            note = parse_note_file(str(md_file), vault_path)
            notes.append(note)
        except Exception:
            continue

    return notes


def _should_skip(file_path: Path, vault: Path) -> bool:
    if file_path.name.startswith("."):
        return True
    parts = file_path.relative_to(vault).parts
    if any(p.startswith(".") or p in SKIP_DIRS for p in parts):
        return True
    return False


def parse_note_file(file_path: str, vault_path: str) -> Note:
    """Parse a single .md file into a Note object."""
    path = Path(file_path)
    rel_path = str(path.relative_to(vault_path))

    with open(file_path, encoding="utf-8") as f:
        post = frontmatter.load(f)

    content = post.content or ""
    fm = post.metadata or {}

    title = fm.get("title", "")
    if not title:
        h1_match = re.search(r"^#\s+(.+)", content, re.MULTILINE)
        title = h1_match.group(1).strip() if h1_match else path.stem

    fm_tags = fm.get("tags", [])
    if isinstance(fm_tags, str):
        fm_tags = [t.strip() for t in fm_tags.split(",")]
    all_tags = list(set(fm_tags + extract_tags(content)))

    links = extract_wikilinks(content)
    sections = _split_sections(content)

    return Note(
        path=rel_path,
        title=title,
        tags=all_tags,
        links=links,
        content=content,
        sections=sections,
    )
