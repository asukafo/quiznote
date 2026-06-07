"""FastAPI web interface — QuizNote v0.4.

Four learning modes:
  Quiz    — Generate & answer questions (SM-2 spaced repetition)
  Present — Knowledge outline: user fills in, AI reviews
  DeepDive — Progressive follow-up questions, drill deeper
  Review  — SM-2 due cards only
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import load_config
from .storage import Storage
from .parser import parse_vault, list_vault_tree, count_md_files, parse_note_file
from .generator import QuestionGenerator
from .quiz import create_quiz, QuizSession
from .grader import grade_answer, compute_score, AIGrader
from .srs import sm2_quality, sm2_update, mastery_level
from .models import QuestionType, new_id, Quiz

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

jinja = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)

_sessions: dict[str, QuizSession] = {}
_generation_tasks: dict[str, dict] = {}
_deepdive_sessions: dict[str, dict] = {}
_present_sessions: dict[str, dict] = {}


def create_app() -> FastAPI:
    app = FastAPI(title="QuizNote", version="0.4.0")
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
    return QuestionGenerator(model=load_config().anthropic_model)


def _get_ai_grader():
    return AIGrader(model=load_config().anthropic_model)


def _render(template: str, request: Request, **kwargs) -> HTMLResponse:
    tmpl = jinja.get_template(template)
    return HTMLResponse(tmpl.render(request=request, **kwargs))


def _call_claude(prompt: str, schema: dict | None = None, budget: float = 1.0) -> dict:
    """Synchronous claude -p call returning structured_output."""
    import subprocess
    cmd = ["claude", "-p", prompt, "--model", "sonnet",
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
    parts = []
    for fp in file_paths[:10]:
        full = Path(vault_path) / fp
        if full.exists() and full.suffix == ".md":
            text = full.read_text(encoding="utf-8")[:8000]
            parts.append(f"## {fp}\n\n{text}")
    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Background generation
# ---------------------------------------------------------------------------

def _run_generation(task_id: str, file_paths: list[str], count: int,
                    question_types: list[QuestionType], difficulty: str, vault_path: str):
    import traceback
    try:
        _generation_tasks[task_id]["status"] = "calling_claude"
        gen = QuestionGenerator()
        all_questions = []
        for i in range(0, len(file_paths), 3):
            batch = file_paths[i:i + 3]
            notes = []
            for fp in batch:
                try:
                    note = parse_note_file(str(Path(vault_path) / fp), vault_path)
                    notes.append(note)
                except Exception:
                    continue
            if not notes:
                continue
            batch_count = max(1, count * len(batch) // len(file_paths))
            all_questions.extend(
                gen.generate(notes, count=batch_count, question_types=question_types, difficulty=difficulty)
            )
        _generation_tasks[task_id]["status"] = "saving"
        _, storage = _get_cfg_storage()
        storage.save_questions(all_questions)
        _generation_tasks[task_id]["status"] = "done"
        _generation_tasks[task_id]["questions"] = all_questions
    except Exception as e:
        _generation_tasks[task_id]["status"] = "error"
        _generation_tasks[task_id]["error"] = str(e)
        _generation_tasks[task_id]["traceback"] = traceback.format_exc()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _setup_routes(app: FastAPI):

    # ==================================================================
    # DASHBOARD
    # ==================================================================

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

    # ==================================================================
    # MAIN PAGE
    # ==================================================================

    @app.get("/", response_class=HTMLResponse)
    async def vault(request: Request, show_generated: str = ""):
        cfg, storage = _get_cfg_storage()
        tree = list_vault_tree(cfg.vault_path)
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
        return RedirectResponse("/", status_code=303)

    # ==================================================================
    # UNIFIED GENERATE — routes to mode
    # ==================================================================

    @app.post("/generate", response_class=HTMLResponse)
    async def generate(request: Request, count: int = Form(10),
                       qtype: str = Form("all"), difficulty: str = Form("mixed"),
                       mode: str = Form("quiz")):
        cfg, storage = _get_cfg_storage()
        form = await request.form()
        selected_paths = form.getlist("file_paths")
        if not selected_paths:
            tree = list_vault_tree(cfg.vault_path)
            return _render("vault.html", request, tree=tree,
                           total_files=count_md_files(tree),
                           total_questions=storage.count_questions(),
                           stats=storage.get_stats(),
                           srs_stats=storage.get_srs_stats(),
                           error="请至少选择一个文件或目录。")

        if mode == "present":
            return await _start_present(request, cfg, storage, selected_paths)
        elif mode == "deepdive":
            return await _start_deepdive(request, cfg, storage, selected_paths)
        else:
            return await _start_quiz(request, cfg, storage, selected_paths, count, qtype, difficulty)

    async def _start_quiz(request, cfg, storage, file_paths, count, qtype, difficulty):
        type_map = {"mc": "multiple_choice", "tf": "true_false", "code": "programming",
                    "short": "short_answer", "fill": "fill_blank"}
        if qtype == "all":
            question_types: list[QuestionType] = [
                "multiple_choice", "true_false", "programming", "short_answer", "fill_blank"
            ]
        else:
            question_types = [type_map.get(t.strip(), "multiple_choice") for t in qtype.split(",")]  # type: ignore

        task_id = new_id()
        _generation_tasks[task_id] = {"status": "running", "questions": None, "file_paths": file_paths}
        thread = threading.Thread(target=_run_generation,
                                  args=(task_id, file_paths, count, question_types, difficulty, cfg.vault_path),
                                  daemon=True)
        thread.start()
        display_paths = [p.replace(".md", "") for p in file_paths[:5]]
        return _render("loading.html", request, task_id=task_id,
                       headings=display_paths, count=count, qtype=qtype, difficulty=difficulty)

    @app.get("/generate/status/{task_id}")
    async def generate_status(task_id: str):
        task = _generation_tasks.get(task_id)
        if task is None:
            return JSONResponse({"status": "not_found", "count": 0, "error": "", "traceback": ""})
        questions = task.get("questions")
        count = len(questions) if questions else 0
        return JSONResponse({"status": task["status"], "count": count,
                             "error": task.get("error", ""), "traceback": task.get("traceback", "")})

    # ==================================================================
    # PRESENT MODE
    # ==================================================================

    async def _start_present(request: Request, cfg, storage, file_paths: list[str]):
        content = _read_files_content(file_paths, cfg.vault_path)
        if not content.strip():
            tree = list_vault_tree(cfg.vault_path)
            return _render("vault.html", request, tree=tree, error="选中的文件没有内容。")

        prompt = f"""你是知识脉络设计师。设计一个知识填空框架让学习者填充。

笔记：{content[:12000]}

输出 JSON：{{"title":"主题","sections":[{{"heading":"标题","prompt":"引导语","key_points":["填空提示1"],"expected_keywords":["关键词1"]}}]}}

规则：4-8个section，从概括到具体；prompt要有引导性不要太直白；key_points是填空提示；expected_keywords是评判关键词。"""

        try:
            data = _call_claude(prompt, {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "sections": {"type": "array", "items": {
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string"}, "prompt": {"type": "string"},
                            "key_points": {"type": "array", "items": {"type": "string"}},
                            "expected_keywords": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["heading", "prompt", "key_points", "expected_keywords"]
                    }}
                },
                "required": ["title", "sections"]
            })
        except Exception as e:
            tree = list_vault_tree(cfg.vault_path)
            return _render("vault.html", request, tree=tree, error=f"生成脉络失败: {e}")

        session_id = new_id()
        storage.save_session(session_id, "present", file_paths)
        _present_sessions[session_id] = {"data": data, "file_paths": file_paths}
        return _render("present.html", request, session_id=session_id,
                       title=data.get("title", ""), sections=data.get("sections", []))

    @app.post("/present/{session_id}/review", response_class=HTMLResponse)
    async def present_review(request: Request, session_id: str):
        session = _present_sessions.get(session_id)
        if not session:
            return _render("error.html", request, message="Session not found")

        form = await request.form()
        data = session["data"]
        sections = data.get("sections", [])
        user_answers = {}
        for i, sec in enumerate(sections):
            key = f"answer_{i}"
            if key in form:
                user_answers[str(i)] = form[key]

        review_text = ""
        for i, sec in enumerate(sections):
            ua = user_answers.get(str(i), "(未作答)")
            expected = ", ".join(sec.get("expected_keywords", []))
            review_text += f"### {sec['heading']}\n引导: {sec['prompt']}\n要点: {', '.join(sec.get('key_points', []))}\n期望关键词: {expected}\n学习者作答: {ua}\n\n"

        review_prompt = f"""你是学习评估专家。逐section评审学习者的知识填充。

{review_text}

输出JSON：{{"overall_score":85,"overall_feedback":"总评","results":[{{"section_idx":0,"heading":"","score":80,"feedback":"","correct":["对的地方"],"missing":["漏掉的关键点"],"suggestion":"改进建议"}}]}}"""

        try:
            review = _call_claude(review_prompt, {
                "type": "object",
                "properties": {
                    "overall_score": {"type": "number"}, "overall_feedback": {"type": "string"},
                    "results": {"type": "array", "items": {
                        "type": "object",
                        "properties": {
                            "section_idx": {"type": "integer"}, "heading": {"type": "string"},
                            "score": {"type": "number"}, "feedback": {"type": "string"},
                            "correct": {"type": "array", "items": {"type": "string"}},
                            "missing": {"type": "array", "items": {"type": "string"}},
                            "suggestion": {"type": "string"},
                        },
                        "required": ["section_idx", "heading", "score", "feedback"]
                    }}
                },
                "required": ["overall_score", "overall_feedback", "results"]
            })
        except Exception as e:
            review = {"overall_score": 0, "overall_feedback": f"评审出错: {e}", "results": []}

        _, storage = _get_cfg_storage()
        storage.complete_session(session_id)
        return _render("present_result.html", request, title=data.get("title", ""),
                       review=review, sections=sections, user_answers=user_answers)

    # ==================================================================
    # DEEP DIVE MODE
    # ==================================================================

    async def _start_deepdive(request: Request, cfg, storage, file_paths: list[str]):
        content = _read_files_content(file_paths, cfg.vault_path)
        if not content.strip():
            tree = list_vault_tree(cfg.vault_path)
            return _render("vault.html", request, tree=tree, error="选中的文件没有内容。")

        prompt = f"""你是刨根问底的学习教练。根据以下笔记设计第一个深挖问题。

笔记：{content[:10000]}

输出JSON：{{"topic":"核心知识点","question":"第一个问题（有思考深度）","hint":"引导提示","depth":1}}"""

        try:
            data = _call_claude(prompt, {
                "type": "object",
                "properties": {"topic": {"type": "string"}, "question": {"type": "string"},
                               "hint": {"type": "string"}, "depth": {"type": "integer"}},
                "required": ["topic", "question", "hint", "depth"]
            }, budget=0.5)
        except Exception as e:
            data = {"topic": "Error", "question": f"生成失败: {e}", "hint": "", "depth": 1}

        session_id = new_id()
        storage.save_session(session_id, "deepdive", file_paths)
        _deepdive_sessions[session_id] = {
            "topic": data["topic"], "file_paths": file_paths,
            "vault_path": cfg.vault_path, "depth": 1, "max_depth": 8,
            "history": [{"depth": 1, "question": data["question"], "answer": None, "followup": None}],
            "content": content[:10000],
        }
        return _render("deepdive.html", request, session_id=session_id,
                       topic=data["topic"], question=data["question"],
                       hint=data["hint"], depth=1, max_depth=8, history=[])

    @app.post("/deepdive/{session_id}/answer", response_class=HTMLResponse)
    async def deepdive_answer(request: Request, session_id: str):
        session = _deepdive_sessions.get(session_id)
        if not session:
            return _render("error.html", request, message="Session not found")

        form = await request.form()
        user_answer = form.get("answer", "")
        action = form.get("action", "continue")
        current = session["history"][-1]
        current["answer"] = user_answer

        if action == "summary" or session["depth"] >= session["max_depth"]:
            summary_prompt = f"""学习者完成刨根问底学习。主题：{session['topic']}

对话：{chr(10).join(f"Q{h['depth']}: {h['question']}\\nA: {h['answer'] or '(未答)'}" for h in session['history'])}

输出JSON：{{"overall_feedback":"...","mastered":["掌握的"],"gaps":["不足的"],"suggestions":["建议"]}}"""

            try:
                review = _call_claude(summary_prompt, {
                    "type": "object",
                    "properties": {"overall_feedback": {"type": "string"},
                                   "mastered": {"type": "array", "items": {"type": "string"}},
                                   "gaps": {"type": "array", "items": {"type": "string"}},
                                   "suggestions": {"type": "array", "items": {"type": "string"}}},
                    "required": ["overall_feedback", "mastered", "gaps", "suggestions"]
                })
            except Exception as e:
                review = {"overall_feedback": f"评审出错: {e}", "mastered": [], "gaps": [], "suggestions": []}

            _, storage = _get_cfg_storage()
            storage.complete_session(session_id)
            return _render("deepdive_result.html", request,
                           topic=session["topic"], history=session["history"], review=review)

        # Next deeper question
        next_depth = session["depth"] + 1
        followup_prompt = f"""学习者正在深入学习"{session['topic']}"。对话历史：
{chr(10).join(f"Q{h['depth']}: {h['question']}\\nA: {h['answer']}" for h in session['history'])}

刚回答Q{session['depth']}："用户{u_answer}"

设计第{next_depth}个更深的问题。答得好往底层挖，答得不好换角度追问同一概念。

输出JSON：{{"followup":"问题","hint":"提示","depth":{next_depth},"feedback":"简评"}}"""

        try:
            data = _call_claude(followup_prompt, {
                "type": "object",
                "properties": {"followup": {"type": "string"}, "hint": {"type": "string"},
                               "depth": {"type": "integer"}, "feedback": {"type": "string"}},
                "required": ["followup", "hint", "depth", "feedback"]
            }, budget=0.5)
        except Exception as e:
            data = {"followup": f"生成下一问失败: {e}", "hint": "", "depth": next_depth, "feedback": ""}

        current["followup"] = data.get("feedback", "")
        session["depth"] = next_depth
        session["history"].append({"depth": next_depth, "question": data["followup"],
                                   "answer": None, "followup": None})
        return _render("deepdive.html", request, session_id=session_id,
                       topic=session["topic"], question=data["followup"],
                       hint=data.get("hint", ""), depth=next_depth,
                       max_depth=session["max_depth"], history=session["history"][:-1])

    # ==================================================================
    # SRS REVIEW
    # ==================================================================

    @app.get("/review", response_class=HTMLResponse)
    async def review_mode(request: Request):
        _, storage = _get_cfg_storage()
        due_ids = storage.get_due_srs_questions(20)
        if not due_ids:
            questions = storage.list_questions(limit=10)
            question_ids = [q.id for q in questions]
        else:
            question_ids = due_ids
            questions = [storage.get_question(qid) for qid in due_ids]
            questions = [q for q in questions if q]

        if not questions:
            return _render("vault.html", request, tree=list_vault_tree(load_config().vault_path),
                           error="还没有题目。先出一些题吧。")

        session_id = new_id()
        quiz = Quiz(id=session_id, title="SRS Review", question_ids=[q.id for q in questions],
                     mode="review", total=len(questions))
        storage.save_quiz(quiz)
        _sessions[session_id] = QuizSession(storage, quiz, questions)
        return RedirectResponse(f"/review/{session_id}/0", status_code=303)

    @app.get("/review/{session_id}/{q_idx}", response_class=HTMLResponse)
    async def review_question(request: Request, session_id: str, q_idx: int):
        session = _sessions.get(session_id)
        if session is None: return RedirectResponse("/review", status_code=303)
        if q_idx != session.current_index:
            return RedirectResponse(f"/review/{session_id}/{session.current_index}", status_code=303)
        if session.is_finished:
            return RedirectResponse(f"/review/{session_id}/done", status_code=303)
        q = session.current_question
        _, storage = _get_cfg_storage()
        srs = storage.get_srs_item(q.id)
        srs_info = f"间隔 {srs['interval_days']} 天 | {mastery_level(srs['interval_days'], srs['repetitions'])}" if srs else "新题"
        return _render("review_card.html", request, quiz=session.quiz, question=q,
                       current=session.current_index + 1, total=session.total, srs_info=srs_info)

    @app.post("/review/{session_id}/answer", response_class=HTMLResponse)
    async def review_answer(request: Request, session_id: str,
                            answer: str = Form(""), confidence: str = Form("medium")):
        session = _sessions.get(session_id)
        if session is None: return RedirectResponse("/review", status_code=303)
        q = session.current_question
        if q is None: return RedirectResponse(f"/review/{session_id}/done", status_code=303)

        ai_grader = _get_ai_grader()
        answer_obj = grade_answer(q, answer, ai_grader)
        answer_obj.quiz_id = session_id
        session.submit_answer(answer)

        _, storage = _get_cfg_storage()
        storage.save_answer(answer_obj)
        storage.update_topic_stat(q.topic, answer_obj.is_correct or False)

        quality = sm2_quality(answer_obj.is_correct or False, confidence)
        srs = storage.get_srs_item(q.id)
        reps, ef, interval = (srs["repetitions"], srs["ease_factor"], srs["interval_days"]) if srs else (0, 2.5, 0.0)
        new_reps, new_ef, new_interval, next_date = sm2_update(quality, reps, ef, interval)
        storage.upsert_srs(q.id, new_reps, new_ef, new_interval, next_date, is_lapse=(quality < 3))

        if session.is_finished:
            session.finish()
            storage.complete_quiz(session_id, compute_score(session.answers))
            return RedirectResponse(f"/review/{session_id}/done", status_code=303)
        return RedirectResponse(f"/review/{session_id}/{session.current_index}", status_code=303)

    @app.get("/review/{session_id}/done", response_class=HTMLResponse)
    async def review_done(request: Request, session_id: str):
        session = _sessions.get(session_id)
        _, storage = _get_cfg_storage()
        answers = session.answers if session else []
        score = compute_score(answers) if answers else 0
        correct = sum(1 for a in answers if a.is_correct)
        srs_stats = storage.get_srs_stats()
        return _render("review_done.html", request, score=score,
                       correct=correct, total=len(answers), srs_stats=srs_stats)

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
        if q is None: return _render("error.html", request, message="Question not found")
        return _render("question_edit.html", request, question=q)

    @app.post("/question/{qid}/edit", response_class=HTMLResponse)
    async def question_edit_save(request: Request, qid: str, question: str = Form(""),
                                 correct_answer: str = Form(""), explanation: str = Form(""),
                                 difficulty: str = Form(""), topic: str = Form("")):
        _, storage = _get_cfg_storage()
        fields = {}
        for k, v in [("question", question), ("correct_answer", correct_answer),
                      ("explanation", explanation), ("difficulty", difficulty), ("topic", topic)]:
            if v: fields[k] = v
        if fields: storage.update_question(qid, **fields)
        return RedirectResponse("/", status_code=303)

    # ==================================================================
    # Quiz (legacy)
    # ==================================================================

    @app.get("/quiz/start", response_class=HTMLResponse)
    async def quiz_start_form(request: Request):
        _, storage = _get_cfg_storage()
        return _render("quiz_start.html", request, topics=storage.get_all_topics())

    @app.post("/quiz/start", response_class=HTMLResponse)
    async def quiz_start(request: Request, mode: str = Form("random"),
                         topic: str = Form(""), count: int = Form(10)):
        _, storage = _get_cfg_storage()
        try:
            session = create_quiz(storage, mode=mode, topic=topic if topic else None, count=count)  # type: ignore
        except ValueError as e:
            return _render("quiz_start.html", request, topics=storage.get_all_topics(), error=str(e))
        _sessions[session.quiz.id] = session
        return RedirectResponse(f"/quiz/{session.quiz.id}/0", status_code=303)

    @app.get("/quiz/{quiz_id}/{q_idx}", response_class=HTMLResponse)
    async def quiz_question(request: Request, quiz_id: str, q_idx: int):
        session = _sessions.get(quiz_id)
        _, storage = _get_cfg_storage()
        if session is None:
            quiz = storage.get_quiz(quiz_id)
            if quiz is None: return _render("error.html", request, message="Quiz not found")
            questions = [storage.get_question(qid) for qid in quiz.question_ids]
            questions = [q for q in questions if q]
            session = QuizSession(storage, quiz, questions)
            session._current_index = len(storage.get_answers_for_quiz(quiz_id))
            _sessions[quiz_id] = session
        if q_idx != session.current_index:
            return RedirectResponse(f"/quiz/{quiz_id}/{session.current_index}", status_code=303)
        if session.is_finished:
            return RedirectResponse(f"/quiz/{quiz_id}/result", status_code=303)
        return _render("quiz.html", request, quiz=session.quiz, question=session.current_question,
                       current=session.current_index + 1, total=session.total)

    @app.post("/quiz/{quiz_id}/answer", response_class=HTMLResponse)
    async def quiz_answer(request: Request, quiz_id: str, answer: str = Form("")):
        session = _sessions.get(quiz_id)
        if session is None: return RedirectResponse("/", status_code=303)
        q = session.current_question
        if q is None: return RedirectResponse(f"/quiz/{quiz_id}/result", status_code=303)
        answer_obj = grade_answer(q, answer, _get_ai_grader())
        answer_obj.quiz_id = quiz_id
        session.submit_answer(answer)
        _, storage = _get_cfg_storage()
        storage.save_answer(answer_obj)
        storage.update_topic_stat(q.topic, answer_obj.is_correct or False)
        # SRS
        quality = sm2_quality(answer_obj.is_correct or False, "medium")
        srs = storage.get_srs_item(q.id)
        reps, ef, interval = (srs["repetitions"], srs["ease_factor"], srs["interval_days"]) if srs else (0, 2.5, 0.0)
        nr, ne, ni, nd = sm2_update(quality, reps, ef, interval)
        storage.upsert_srs(q.id, nr, ne, ni, nd, is_lapse=(quality < 3))
        if session.is_finished:
            session.finish()
            storage.complete_quiz(quiz_id, compute_score(session.answers))
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
            answers = storage.get_answers_for_quiz(quiz_id) if quiz else []
        score = compute_score(answers) if answers else 0
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
        return _render("stats.html", request, stats=storage.get_stats(),
                       srs_stats=storage.get_srs_stats())
