"""Legacy quiz routes — topic/random/weakest/exam quiz flow."""

from __future__ import annotations

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..quiz import create_quiz, QuizSession
from ..grader import grade_answer, compute_score
from ..srs import sm2_quality, sm2_update
from ..models import QuestionType
from . import _get_cfg_storage, _get_ai_grader, _render, _sessions


def register_routes(app: FastAPI) -> None:

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
            if quiz is None:
                return _render("error.html", request, message="Quiz not found")
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
        if session is None:
            return RedirectResponse("/", status_code=303)
        q = session.current_question
        if q is None:
            return RedirectResponse(f"/quiz/{quiz_id}/result", status_code=303)
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
            if q:
                question_map[qid] = q
        return _render("result.html", request, quiz=quiz, answers=answers,
                       question_map=question_map, score=score)
