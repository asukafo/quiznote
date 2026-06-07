"""Present mode — AI builds a knowledge outline, user fills it in, AI reviews."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from ..models import new_id
from . import (
    _get_cfg_storage, _get_cached_tree, _render,
    _call_claude, _read_files_content, _present_sessions,
)


async def _start_present(request: Request, cfg, storage, file_paths: list[str]):
    content = _read_files_content(file_paths, cfg.vault_path)
    if not content.strip():
        tree = _get_cached_tree(cfg.vault_path)
        return _render("vault.html", request, tree=tree, error="选中的文件没有内容。")

    prompt = f"""你是知识脉络设计师。设计一个知识填空框架让学习者填充。

笔记：{content[:12000]}

输出 JSON：{{"title":"主题","sections":[{{"heading":"标题","prompt":"引导语","key_points":["填空提示1"],"expected_keywords":["关键词1"]}}]}}

规则：4-8个section，从概括到具体；prompt要有引导性不要太直白；key_points是填空提示；expected_keywords是评判关键词。"""

    try:
        from . import _call_claude
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
        tree = _get_cached_tree(cfg.vault_path)
        return _render("vault.html", request, tree=tree, error=f"生成脉络失败: {e}")

    session_id = new_id()
    session_data = {"data": data, "file_paths": file_paths}
    storage.save_session(session_id, "present", file_paths, notes=session_data)
    _present_sessions[session_id] = session_data
    return _render("present.html", request, session_id=session_id,
                   title=data.get("title", ""), sections=data.get("sections", []))


def register_routes(app: FastAPI) -> None:

    @app.post("/present/{session_id}/review", response_class=HTMLResponse)
    async def present_review(request: Request, session_id: str):
        session = _present_sessions.get(session_id)
        if not session:
            # Try restoring from DB
            _, storage = _get_cfg_storage()
            db_sessions = storage.get_sessions(limit=50)
            for s in db_sessions:
                if s["id"] == session_id and s["mode"] == "present":
                    session = s.get("notes", {})
                    if session:
                        _present_sessions[session_id] = session
                    break
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
            review_text += (
                f"### {sec['heading']}\n"
                f"引导: {sec['prompt']}\n"
                f"要点: {', '.join(sec.get('key_points', []))}\n"
                f"期望关键词: {expected}\n"
                f"学习者作答: {ua}\n\n"
            )

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
