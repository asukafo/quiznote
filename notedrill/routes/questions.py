"""Question CRUD routes."""

from __future__ import annotations

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import _get_cfg_storage, _render


def register_routes(app: FastAPI) -> None:

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
    async def question_edit_save(request: Request, qid: str, question: str = Form(""),
                                 correct_answer: str = Form(""), explanation: str = Form(""),
                                 difficulty: str = Form(""), topic: str = Form("")):
        _, storage = _get_cfg_storage()
        fields = {}
        for k, v in [("question", question), ("correct_answer", correct_answer),
                      ("explanation", explanation), ("difficulty", difficulty), ("topic", topic)]:
            if v:
                fields[k] = v
        if fields:
            storage.update_question(qid, **fields)
        return RedirectResponse("/", status_code=303)
