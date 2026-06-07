"""SRS review mode — SM-2 due cards only."""

from __future__ import annotations

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import load_config
from ..grader import grade_answer, compute_score
from ..srs import sm2_quality, sm2_update, mastery_level
from ..models import new_id, Quiz
from ..quiz import QuizSession
from . import (
    _get_cfg_storage, _get_ai_grader, _get_cached_tree, _render,
    _sessions,
)


def register_routes(app: FastAPI) -> None:

    @app.get("/review", response_class=HTMLResponse)
    async def review_mode(request: Request):
        _, storage = _get_cfg_storage()
        due_ids = storage.get_due_srs_questions(20)
        if not due_ids:
            # No due cards — pick fresh questions that haven't been reviewed yet
            questions = storage.list_questions(limit=20)
            questions = [q for q in questions if storage.get_srs_item(q.id) is None]
            if not questions:
                # Truly nothing new — pick any 10
                questions = storage.list_questions(limit=10)
            else:
                questions = questions[:10]
        else:
            questions = [storage.get_question(qid) for qid in due_ids]
            questions = [q for q in questions if q]

        if not questions:
            return _render("vault.html", request, tree=_get_cached_tree(load_config().vault_path),
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
        if session is None:
            return RedirectResponse("/review", status_code=303)
        if q_idx != session.current_index:
            return RedirectResponse(f"/review/{session_id}/{session.current_index}", status_code=303)
        if session.is_finished:
            return RedirectResponse(f"/review/{session_id}/done", status_code=303)
        q = session.current_question
        _, storage = _get_cfg_storage()
        srs = storage.get_srs_item(q.id)
        srs_info = (
            f"间隔 {srs['interval_days']} 天 | {mastery_level(srs['interval_days'], srs['repetitions'])}"
            if srs else "新题"
        )
        return _render("review_card.html", request, quiz=session.quiz, question=q,
                       current=session.current_index + 1, total=session.total, srs_info=srs_info)

    @app.post("/review/{session_id}/answer", response_class=HTMLResponse)
    async def review_answer(request: Request, session_id: str,
                            answer: str = Form(""), confidence: str = Form("medium")):
        session = _sessions.get(session_id)
        if session is None:
            return RedirectResponse("/review", status_code=303)
        q = session.current_question
        if q is None:
            return RedirectResponse(f"/review/{session_id}/done", status_code=303)

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
