"""DeepDive mode — progressive follow-up questions drill deeper layer by layer."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from ..models import new_id
from . import (
    _get_cfg_storage, _get_cached_tree, _render,
    _call_claude, _read_files_content, _deepdive_sessions,
)


async def _start_deepdive(request: Request, cfg, storage, file_paths: list[str]):
    content = _read_files_content(file_paths, cfg.vault_path)
    if not content.strip():
        tree = _get_cached_tree(cfg.vault_path)
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
    session_data = {
        "topic": data["topic"], "file_paths": file_paths,
        "vault_path": cfg.vault_path, "depth": 1, "max_depth": 8,
        "history": [{"depth": 1, "question": data["question"], "answer": None, "followup": None}],
        "content": content[:10000],
    }
    storage.save_session(session_id, "deepdive", file_paths, notes=session_data)
    _deepdive_sessions[session_id] = session_data
    return _render("deepdive.html", request, session_id=session_id,
                   topic=data["topic"], question=data["question"],
                   hint=data["hint"], depth=1, max_depth=8, history=[])


def register_routes(app: FastAPI) -> None:

    @app.post("/deepdive/{session_id}/answer", response_class=HTMLResponse)
    async def deepdive_answer(request: Request, session_id: str):
        session = _deepdive_sessions.get(session_id)
        if not session:
            # Try restoring from DB
            _, storage = _get_cfg_storage()
            db_sessions = storage.get_sessions(limit=50)
            for s in db_sessions:
                if s["id"] == session_id and s["mode"] == "deepdive":
                    session = s.get("notes", {})
                    if session:
                        _deepdive_sessions[session_id] = session
                    break
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

刚回答Q{session['depth']}："{user_answer}"

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

        # Persist session state
        _, storage2 = _get_cfg_storage()
        storage2.save_session(session_id, "deepdive", session.get("file_paths", []), notes=session)

        return _render("deepdive.html", request, session_id=session_id,
                       topic=session["topic"], question=data["followup"],
                       hint=data.get("hint", ""), depth=next_depth,
                       max_depth=session["max_depth"], history=session["history"][:-1])
