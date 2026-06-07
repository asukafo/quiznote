"""Vault browsing, dashboard, and stats routes."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import load_config
from ..parser import parse_vault, count_md_files
from ..models import note_sections_to_dicts
from . import (
    _get_cfg_storage, _get_cached_tree, _invalidate_tree_cache, _render,
    _generation_tasks,
)


def register_routes(app: FastAPI) -> None:

    @app.get("/", response_class=HTMLResponse)
    async def vault(request: Request, show_generated: str = ""):
        cfg, storage = _get_cfg_storage()
        tree = _get_cached_tree(cfg.vault_path)
        total_files = count_md_files(tree)
        total_questions = storage.count_questions()
        stats = storage.get_stats()
        srs_stats = storage.get_srs_stats()
        generated = _generation_tasks.get(show_generated, {}).get("questions") if show_generated else None
        return _render("vault.html", request,
                       tree=tree, total_files=total_files,
                       total_questions=total_questions, stats=stats,
                       srs_stats=srs_stats, generated=generated)

    @app.post("/reparse", response_class=HTMLResponse)
    async def reparse(request: Request):
        cfg, storage = _get_cfg_storage()
        notes = parse_vault(cfg.vault_path)
        for note in notes:
            storage.save_note(note)
            storage.save_sections(note.path, [
                (s.id, s.heading, s.level, s.content, s.code_blocks)
                for s in note.sections
            ])
        _invalidate_tree_cache()
        return RedirectResponse("/", status_code=303)

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request):
        cfg, storage = _get_cfg_storage()
        stats = storage.get_stats()
        srs_stats = storage.get_srs_stats()
        review_history = storage.get_review_history(30)
        recent_sessions = storage.get_sessions(10)
        heatmap = {h["day"]: h["count"] for h in review_history}
        return _render("dashboard.html", request,
                       stats=stats, srs_stats=srs_stats,
                       heatmap=heatmap, recent_sessions=recent_sessions,
                       due_count=srs_stats["due"])

    @app.get("/stats", response_class=HTMLResponse)
    async def stats_page(request: Request):
        _, storage = _get_cfg_storage()
        return _render("stats.html", request, stats=storage.get_stats(),
                       srs_stats=storage.get_srs_stats())
