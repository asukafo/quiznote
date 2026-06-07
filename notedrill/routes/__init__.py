"""Route modules for NoteDrill web interface.

Each module has a register_routes(app) function that registers its
FastAPI route handlers. Shared session state and helpers live here.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import load_config, Config
from ..storage import Storage
from ..parser import list_vault_tree, count_md_files
from ..generator import QuestionGenerator, QuestionCritic, sections_to_text
from ..quiz import QuizSession
from ..grader import AIGrader
from ..models import new_id, Quiz

# ---------------------------------------------------------------------------
# Jinja2 environment (shared across all routes)
# ---------------------------------------------------------------------------

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
jinja = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)

# ---------------------------------------------------------------------------
# Session / task state (process memory — survives one server lifetime)
# ---------------------------------------------------------------------------

_sessions: dict[str, QuizSession] = {}
_generation_tasks: dict[str, dict] = {}
_deepdive_sessions: dict[str, dict] = {}
_present_sessions: dict[str, dict] = {}
_vault_tree_cache: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_cfg_storage() -> tuple[Config, Storage]:
    cfg = load_config()
    storage = Storage(cfg.resolve_db_path())
    storage.init_db()
    storage.migrate()
    return cfg, storage


def _get_ai_grader() -> AIGrader:
    return AIGrader(model=load_config().anthropic_model)


def _render(template: str, request: Request, **kwargs: Any) -> HTMLResponse:
    tmpl = jinja.get_template(template)
    return HTMLResponse(
        tmpl.render(request=request, **kwargs),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


def _get_cached_tree(vault_path: str) -> dict:
    """Return cached vault tree, rebuilding only if vault mtime changed."""
    import os as _os
    try:
        current_mtime = _os.path.getmtime(vault_path)
    except OSError:
        current_mtime = 0

    if (_vault_tree_cache.get("vault_path") == vault_path
            and _vault_tree_cache.get("mtime") == current_mtime
            and _vault_tree_cache.get("tree") is not None):
        return _vault_tree_cache["tree"]

    tree = list_vault_tree(vault_path)
    _vault_tree_cache["tree"] = tree
    _vault_tree_cache["mtime"] = current_mtime
    _vault_tree_cache["vault_path"] = vault_path
    return tree


def _invalidate_tree_cache() -> None:
    _vault_tree_cache.clear()


def _call_claude(prompt: str, schema: dict | None = None, budget: float = 1.0) -> dict:
    """Synchronous claude -p call returning structured_output."""
    model = load_config().anthropic_model or "sonnet"
    cmd = ["claude", "-p", prompt, "--model", model,
           "--output-format", "json", "--max-budget-usd", str(budget)]
    if schema:
        cmd += ["--json-schema", json.dumps(schema)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    raw = result.stdout if result.returncode == 0 else (result.stdout + result.stderr)
    data = json.loads(raw)
    if isinstance(data, dict) and "structured_output" in data:
        return data["structured_output"]
    return data


def _read_files_content(file_paths: list[str], vault_path: str) -> str:
    parts: list[str] = []
    for fp in file_paths[:10]:
        full = Path(vault_path) / fp
        if full.exists() and full.suffix == ".md":
            text = full.read_text(encoding="utf-8")[:8000]
            parts.append(f"## {fp}\n\n{text}")
    return "\n\n---\n\n".join(parts)
