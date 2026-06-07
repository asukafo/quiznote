"""Question generation routes — background task + polling status."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..parser import parse_note_file, count_md_files
from ..generator import QuestionGenerator, QuestionCritic, sections_to_text
from ..models import QuestionType, new_id, QUESTION_TYPE_MAP, ALL_QUESTION_TYPES, note_sections_to_dicts
from . import (
    _get_cfg_storage, _get_cached_tree, _render,
    _generation_tasks,
)


# ---------------------------------------------------------------------------
# Background generation worker
# ---------------------------------------------------------------------------

def _run_generation(task_id: str, file_paths: list[str], count: int,
                    question_types: list[QuestionType], difficulty: str, vault_path: str):
    import traceback
    try:
        _generation_tasks[task_id]["status"] = "calling_claude"
        gen = QuestionGenerator()
        all_questions: list = []
        all_sections: list[dict] = []
        for i in range(0, len(file_paths), 3):
            batch = file_paths[i:i + 3]
            notes = []
            for fp in batch:
                try:
                    note = parse_note_file(str(Path(vault_path) / fp), vault_path)
                    notes.append(note)
                    all_sections.extend(note_sections_to_dicts(note))
                except Exception:
                    continue
            if not notes:
                continue
            batch_count = max(1, count * len(batch) // len(file_paths))
            all_questions.extend(
                gen.generate(notes, count=batch_count, question_types=question_types, difficulty=difficulty)
            )

        # Critic review step
        critic_summary = None
        if all_questions:
            _generation_tasks[task_id]["status"] = "critic_review"
            try:
                sections_text = sections_to_text(all_sections)
                critic = QuestionCritic()
                all_questions, critic_summary = critic.review(all_questions, sections_text)
            except Exception:
                pass  # Fail open — keep all questions

        _generation_tasks[task_id]["status"] = "saving"
        _, storage = _get_cfg_storage()
        storage.save_questions(all_questions)
        _generation_tasks[task_id]["status"] = "done"
        _generation_tasks[task_id]["questions"] = all_questions
        _generation_tasks[task_id]["critic_summary"] = critic_summary
    except Exception as e:
        _generation_tasks[task_id]["status"] = "error"
        _generation_tasks[task_id]["error"] = str(e)
        _generation_tasks[task_id]["traceback"] = traceback.format_exc()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def register_routes(app: FastAPI) -> None:

    @app.post("/generate", response_class=HTMLResponse)
    async def generate(request: Request, count: int = Form(10),
                       qtype: str = Form("all"), difficulty: str = Form("mixed"),
                       mode: str = Form("quiz")):
        cfg, storage = _get_cfg_storage()
        form = await request.form()
        selected_paths = form.getlist("file_paths")
        if not selected_paths:
            tree = _get_cached_tree(cfg.vault_path)
            return _render("vault.html", request, tree=tree,
                           total_files=count_md_files(tree),
                           total_questions=storage.count_questions(),
                           stats=storage.get_stats(),
                           srs_stats=storage.get_srs_stats(),
                           error="请至少选择一个文件或目录。")

        # Import here to avoid circular imports
        from .present import _start_present
        from .deepdive import _start_deepdive

        if mode == "present":
            return await _start_present(request, cfg, storage, selected_paths)
        elif mode == "deepdive":
            return await _start_deepdive(request, cfg, storage, selected_paths)
        else:
            return await _start_quiz(request, cfg, storage, selected_paths, count, qtype, difficulty)

    @app.get("/generate/status/{task_id}")
    async def generate_status(task_id: str):
        task = _generation_tasks.get(task_id)
        if task is None:
            return JSONResponse({"status": "not_found", "count": 0, "error": "", "traceback": "",
                                 "critic_summary": None})
        questions = task.get("questions")
        count = len(questions) if questions else 0
        return JSONResponse({"status": task["status"], "count": count,
                             "error": task.get("error", ""), "traceback": task.get("traceback", ""),
                             "critic_summary": task.get("critic_summary")})


async def _start_quiz(request, cfg, storage, file_paths, count, qtype, difficulty):
    if qtype == "all":
        question_types = list(ALL_QUESTION_TYPES)
    else:
        question_types = [QUESTION_TYPE_MAP.get(t.strip(), "multiple_choice") for t in qtype.split(",")]  # type: ignore

    task_id = new_id()
    _generation_tasks[task_id] = {"status": "running", "questions": None, "file_paths": file_paths}
    thread = threading.Thread(target=_run_generation,
                              args=(task_id, file_paths, count, question_types, difficulty, cfg.vault_path),
                              daemon=True)
    thread.start()
    display_paths = [p.replace(".md", "") for p in file_paths[:5]]
    return _render("loading.html", request, task_id=task_id,
                   headings=display_paths, count=count, qtype=qtype, difficulty=difficulty)
