"""Grading system — evaluates answers and provides feedback.

Auto-grades objective questions (MC, T/F) directly.
Uses Claude Code CLI for subjective questions (programming, short answer).
"""

from __future__ import annotations

import json
import re
import subprocess

from .models import Answer, Question, now

# ---------------------------------------------------------------------------
# JSON Schema for grading output
# ---------------------------------------------------------------------------

GRADE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_correct": {"type": "boolean"},
        "feedback": {"type": "string"},
    },
    "required": ["is_correct", "feedback"],
}


# ---------------------------------------------------------------------------
# Auto-grading (no AI needed)
# ---------------------------------------------------------------------------

def _grade_auto(question: Question, user_answer: str) -> tuple[bool | None, str]:
    """Auto-grade multiple-choice and true-false questions."""
    correct = question.correct_answer.strip()
    user = user_answer.strip()

    is_correct = user.lower() == correct.lower()

    if is_correct:
        feedback = f"✓ 正确！{question.explanation}"
    else:
        feedback = f"✗ 错误。正确答案是「{correct}」。{question.explanation}"

    return is_correct, feedback


# ---------------------------------------------------------------------------
# AI grading via Claude Code CLI
# ---------------------------------------------------------------------------

class AIGrader:
    """Use Claude Code CLI to grade subjective questions."""

    def __init__(self, model: str = "sonnet"):
        self.model = model

    def grade(self, question: Question, user_answer: str) -> tuple[bool | None, str]:
        """Grade a question by shelling out to `claude -p`."""
        prompt = f"""你是严格的编程题和简答题批改专家。根据题目、参考答案和用户作答，评判是否正确。

题目类型：{question.type}
题目：{question.question}
{"代码上下文：" + question.code_context if question.code_context else ""}
参考答案：{question.correct_answer}

用户作答：
{user_answer}

评分标准：
1. 编程题：检查代码逻辑是否正确，不要求一字不差
2. 简答题：检查核心要点是否覆盖，允许不同表述
3. 给出简短、有建设性的中文反馈

请输出 JSON 格式的评判结果。"""

        cmd = [
            "claude", "-p", prompt,
            "--model", self.model,
            "--json-schema", json.dumps(GRADE_SCHEMA),
            "--max-budget-usd", "0.1",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )

            raw_text = result.stdout if result.returncode == 0 else result.stderr

            # Extract JSON object
            fence_match = re.search(r"\{.*?\}", raw_text, re.DOTALL)
            if fence_match:
                data = json.loads(fence_match.group(0))
                return data.get("is_correct", False), data.get("feedback", "")

        except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
            pass

        return None, "（自动批改遇到问题，请人工查看）"


# ---------------------------------------------------------------------------
# Unified grading function
# ---------------------------------------------------------------------------

def grade_answer(
    question: Question,
    user_answer: str,
    ai_grader: AIGrader | None = None,
) -> Answer:
    """Grade a single answer.

    Uses simple comparison for objective questions (MC, T/F),
    and Claude CLI for subjective questions (programming, short answer, fill_blank).
    """
    answer = Answer(
        quiz_id="",
        question_id=question.id,
        question_type=question.type,
        question_text=question.question,
        correct_answer=question.correct_answer,
        user_answer=user_answer,
        answered_at=now(),
    )

    if question.type in ("multiple_choice", "true_false"):
        is_correct, feedback = _grade_auto(question, user_answer)
    elif question.type in ("programming", "short_answer", "fill_blank"):
        if ai_grader:
            is_correct, feedback = ai_grader.grade(question, user_answer)
        else:
            # Fallback: simple substring matching
            correct_lower = question.correct_answer.strip().lower()
            user_lower = user_answer.strip().lower()
            if correct_lower in user_lower or user_lower in correct_lower:
                is_correct = True
                feedback = f"✓ 基本正确。{question.explanation}"
            else:
                is_correct = False
                feedback = f"✗ 参考答案：{question.correct_answer}。{question.explanation}"
    else:
        is_correct, feedback = _grade_auto(question, user_answer)

    answer.is_correct = is_correct
    answer.feedback = feedback
    return answer


def grade_quiz(
    questions: list[Question],
    answers: list[tuple[str, str]],
    ai_grader: AIGrader | None = None,
) -> list[Answer]:
    """Grade all answers for a quiz."""
    question_map = {q.id: q for q in questions}
    graded: list[Answer] = []

    for qid, user_answer in answers:
        q = question_map.get(qid)
        if q is None:
            continue
        answer = grade_answer(q, user_answer, ai_grader)
        graded.append(answer)

    return graded


def compute_score(answers: list[Answer]) -> float:
    """Compute the score as percentage of correct answers."""
    if not answers:
        return 0.0
    correct = sum(1 for a in answers if a.is_correct)
    return round(correct / len(answers) * 100, 1)
