"""FastAPI web interface — file-tree first, select files, generate, quiz."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import load_config
from .storage import Storage
from .parser import parse_vault, list_vault_tree, count_md_files, collect_file_paths, parse_note_file
from .generator import QuestionGenerator
from .quiz import create_quiz, QuizSession
from .grader import grade_answer, compute_score, AIGrader
from .models import QuestionType, new_id

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

jinja = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)

_sessions: dict[str, QuizSession] = {}
_generation_tasks: dict[str, dict] = {}


def create_app() -> FastAPI:
    app = FastAPI(title="QuizNote", version="0.3.0")
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    _setup_routes(app)
    return app


def _get_cfg_storage():
    cfg = load_config()
    storage = Storage(cfg.resolve_db_path())
    storage.init_db()
    return cfg, storage


def _get_generator():
    cfg = load_config()
    return QuestionGenerator(model=cfg.anthropic_model)


def _get_ai_grader():
    cfg = load_config()
    return AIGrader(model=cfg.anthropic_model)


def _render(template: str, request: Request, **kwargs) -> HTMLResponse:
    tmpl = jinja.get_template(template)
    return HTMLResponse(tmpl.render(request=request, **kwargs))


# ---------------------------------------------------------------------------
# Background generation
# ---------------------------------------------------------------------------

def _run_generation(task_id: str, file_paths: list[str], count: int,
                    question_types: list[QuestionType], difficulty: str, vault_path: str):
    """Run generation in background thread, one batch per file."""
    import traceback
    try:
        _generation_tasks[task_id]["status"] = "calling_claude"
        gen = QuestionGenerator()
        all_questions = []

        # Process files in batches to avoid huge prompts
        batch_size = 3  # files per batch
        for i in range(0, len(file_paths), batch_size):
            batch_paths = file_paths[i:i + batch_size]
            # Parse notes for this batch
            notes = []
            for fp in batch_paths:
                full_path = str(Path(vault_path) / fp)
                try:
                    note = parse_note_file(full_path, vault_path)
                    notes.append(note)
                except Exception:
                    continue

            if not notes:
                continue

            # Generate questions proportional to file count in batch
            batch_count = max(1, count * len(batch_paths) // len(file_paths))
            questions = gen.generate(
                notes, count=batch_count, question_types=question_types, difficulty=difficulty
            )
            all_questions.extend(questions)

        _generation_tasks[task_id]["status"] = "saving"

        _, storage = _get_cfg_storage()
        storage.save_questions(all_questions)

        _generation_tasks[task_id]["status"] = "done"
        _generation_tasks[task_id]["questions"] = all_questions
        _generation_tasks[task_id]["file_paths"] = file_paths
    except Exception as e:
        _generation_tasks[task_id]["status"] = "error"
        _generation_tasks[task_id]["error"] = str(e)
        _generation_tasks[task_id]["traceback"] = traceback.format_exc()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _setup_routes(app: FastAPI):

    # ==================================================================
    # MAIN PAGE — file tree
    # ==================================================================

    @app.get("/", response_class=HTMLResponse)
    async def vault(request: Request, show_generated: str = ""):
        cfg, storage = _get_cfg_storage()

        # Always rebuild tree fresh (lightweight, just directory scan)
        tree = list_vault_tree(cfg.vault_path)
        total_files = count_md_files(tree)
        total_questions = storage.count_questions()
        stats = storage.get_stats()

        generated = None
        if show_generated and show_generated in _generation_tasks:
            task = _generation_tasks[show_generated]
            generated = task.get("questions")

        return _render("vault.html", request,
                        tree=tree, total_files=total_files,
                        total_questions=total_questions, stats=stats,
                        generated=generated)

    # ==================================================================
    # Reparse — sync notes into DB
    # ==================================================================

    @app.post("/reparse", response_class=HTMLResponse)
    async def reparse(request: Request):
        cfg, storage = _get_cfg_storage()
        notes = parse_vault(cfg.vault_path)
        for note in notes:
            storage.save_note(note)
            section_tuples = [
                (s.id, s.heading, s.level, s.content, s.code_blocks)
                for s in note.sections
            ]
            storage.save_sections(note.path, section_tuples)
        return RedirectResponse("/", status_code=303)

    # ==================================================================
    # Generate from selected files/directories
    # ==================================================================

    @app.post("/generate", response_class=HTMLResponse)
    async def generate(
        request: Request,
        count: int = Form(10),
        qtype: str = Form("all"),
        difficulty: str = Form("mixed"),
    ):
        cfg, storage = _get_cfg_storage()

        form = await request.form()
        selected_paths = form.getlist("file_paths")

        if not selected_paths:
            tree = list_vault_tree(cfg.vault_path)
            return _render("vault.html", request,
                           tree=tree, total_files=count_md_files(tree),
                           total_questions=storage.count_questions(),
                           stats=storage.get_stats(),
                           error="请至少选择一个文件或目录。")

        # Map question types
        type_map = {
            "mc": "multiple_choice", "tf": "true_false",
            "code": "programming", "short": "short_answer", "fill": "fill_blank",
        }
        if qtype == "all":
            question_types: list[QuestionType] = [
                "multiple_choice", "true_false", "programming", "short_answer", "fill_blank"
            ]
        else:
            question_types = [type_map.get(t.strip(), "multiple_choice") for t in qtype.split(",")]  # type: ignore

        # Start async generation
        task_id = new_id()
        _generation_tasks[task_id] = {
            "status": "running",
            "questions": None,
            "file_paths": selected_paths,
        }

        thread = threading.Thread(
            target=_run_generation,
            args=(task_id, selected_paths, count, question_types, difficulty, cfg.vault_path),
            daemon=True,
        )
        thread.start()

        # show brief loading text from file paths
        display_paths = [p.replace(".md", "") for p in selected_paths[:5]]
        if len(selected_paths) > 5:
            display_paths.append(f"... +{len(selected_paths) - 5} more")

        return _render("loading.html", request,
                       task_id=task_id,
                       headings=display_paths,
                       count=count, qtype=qtype, difficulty=difficulty)

    @app.get("/generate/status/{task_id}")
    async def generate_status(task_id: str):
        task = _generation_tasks.get(task_id)
        if task is None:
            return JSONResponse({"status": "not_found"})
        return JSONResponse({
            "status": task["status"],
            "count": len(task.get("questions", [])),
            "error": task.get("error"),
            "traceback": task.get("traceback", ""),
        })

    # ==================================================================
    # Question CRUD
    # ==================================================================

    @app.post("/question/{qid}/delete", response_class=HTMLResponse)
    async def question_delete(request: Request, qid: str):
        _, storage = _get_cfg_storage()
        storage.delete_question(qid)
        return RedirectResponse("/", status_code=303)

    @app.get("/question/{qid}/edit", response_class=HTMLResponse)
    async def question_edit_form(request: Request, qid: str):
        _, storage = _get_cfg_storage()
        q = storage.get_question(qid)
        if q is None:
            return _render("error.html", request, message="Question not found")
        return _render("question_edit.html", request, question=q)

    @app.post("/question/{qid}/edit", response_class=HTMLResponse)
    async def question_edit_save(
        request: Request,
        qid: str,
        question: str = Form(""),
        correct_answer: str = Form(""),
        explanation: str = Form(""),
        difficulty: str = Form(""),
        topic: str = Form(""),
    ):
        _, storage = _get_cfg_storage()
        fields = {}
        if question: fields["question"] = question
        if correct_answer: fields["correct_answer"] = correct_answer
        if explanation: fields["explanation"] = explanation
        if difficulty: fields["difficulty"] = difficulty
        if topic: fields["topic"] = topic
        if fields:
            storage.update_question(qid, **fields)
        return RedirectResponse("/", status_code=303)

    # ==================================================================
    # Quiz
    # ==================================================================

    @app.get("/quiz/start", response_class=HTMLResponse)
    async def quiz_start_form(request: Request):
        _, storage = _get_cfg_storage()
        topics = storage.get_all_topics()
        return _render("quiz_start.html", request, topics=topics)

    @app.post("/quiz/start", response_class=HTMLResponse)
    async def quiz_start(
        request: Request,
        mode: str = Form("random"),
        topic: str = Form(""),
        count: int = Form(10),
    ):
        _, storage = _get_cfg_storage()
        try:
            session = create_quiz(storage, mode=mode, topic=topic if topic else None, count=count)  # type: ignore
        except ValueError as e:
            topics = storage.get_all_topics()
            return _render("quiz_start.html", request, topics=topics, error=str(e))

        _sessions[session.quiz.id] = session
        return RedirectResponse(f"/quiz/{session.quiz.id}/0", status_code=303)

    @app.get("/quiz/{quiz_id}/{q_idx}", response_class=HTMLResponse)
    async def quiz_question(request: Request, quiz_id: str, q_idx: int):
        session = _sessions.get(quiz_id)
        _, storage = _get_cfg_storage()

        if session is None:
            quiz = storage.get_quiz(quiz_id)
            if quiz is None:
                return _render("error.html", request, message="Quiz not found")
            questions = [storage.get_question(qid) for qid in quiz.question_ids]
            questions = [q for q in questions if q]
            session = QuizSession(storage, quiz, questions)
            answers = storage.get_answers_for_quiz(quiz_id)
            session._current_index = len(answers)
            session._answers = answers
            _sessions[quiz_id] = session

        if q_idx != session.current_index:
            return RedirectResponse(f"/quiz/{quiz_id}/{session.current_index}", status_code=303)
        if session.is_finished:
            return RedirectResponse(f"/quiz/{quiz_id}/result", status_code=303)

        q = session.current_question
        return _render("quiz.html", request, quiz=session.quiz, question=q,
                       current=session.current_index + 1, total=session.total)

    @app.post("/quiz/{quiz_id}/answer", response_class=HTMLResponse)
    async def quiz_answer(request: Request, quiz_id: str, answer: str = Form("")):
        session = _sessions.get(quiz_id)
        if session is None:
            return RedirectResponse("/", status_code=303)

        q = session.current_question
        if q is None:
            return RedirectResponse(f"/quiz/{quiz_id}/result", status_code=303)

        ai_grader = _get_ai_grader()
        answer_obj = grade_answer(q, answer, ai_grader)
        answer_obj.quiz_id = quiz_id
        session.submit_answer(answer)

        _, storage = _get_cfg_storage()
        storage.save_answer(answer_obj)
        storage.update_topic_stat(q.topic, answer_obj.is_correct or False)

        if session.is_finished:
            session.finish()
            score = compute_score(session.answers)
            storage.complete_quiz(quiz_id, score)
            return RedirectResponse(f"/quiz/{quiz_id}/result", status_code=303)
        return RedirectResponse(f"/quiz/{quiz_id}/{session.current_index}", status_code=303)

    @app.get("/quiz/{quiz_id}/result", response_class=HTMLResponse)
    async def quiz_result(request: Request, quiz_id: str):
        session = _sessions.get(quiz_id)
        _, storage = _get_cfg_storage()

        if session:
            answers, quiz = session.answers, session.quiz
        else:
            quiz = storage.get_quiz(quiz_id)
            if quiz is None:
                return _render("error.html", request, message="Quiz not found")
            answers = storage.get_answers_for_quiz(quiz_id)

        score = compute_score(answers)
        question_map = {}
        for qid in (quiz.question_ids if quiz else []):
            q = storage.get_question(qid)
            if q: question_map[qid] = q

        return _render("result.html", request, quiz=quiz, answers=answers,
                       question_map=question_map, score=score)

    # ==================================================================
    # Stats
    # ==================================================================

    @app.get("/stats", response_class=HTMLResponse)
    async def stats_page(request: Request):
        _, storage = _get_cfg_storage()
        stats = storage.get_stats()
        return _render("stats.html", request, stats=stats)
