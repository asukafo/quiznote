"""Data models for NoteDrill."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Note models
# ---------------------------------------------------------------------------

class Section(BaseModel):
    id: str = Field(default_factory=new_id)
    heading: str = ""
    level: int = 1
    content: str = ""
    code_blocks: list[str] = Field(default_factory=list)


class Note(BaseModel):
    path: str  # relative to vault root
    title: str = ""
    tags: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    content: str = ""
    sections: list[Section] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Question models
# ---------------------------------------------------------------------------

QuestionType = Literal[
    "multiple_choice", "true_false", "programming", "short_answer", "fill_blank"
]
Difficulty = Literal["easy", "medium", "hard"]


class Option(BaseModel):
    id: str  # "a", "b", "c", "d"
    text: str


class Question(BaseModel):
    id: str = Field(default_factory=new_id)
    type: QuestionType = "multiple_choice"
    topic: str = ""
    difficulty: Difficulty = "medium"
    question: str = ""
    options: list[Option] | None = None
    code_context: str | None = None
    correct_answer: str = ""
    explanation: str = ""
    source_note: str = ""
    source_section: str = ""  # section ID
    created_at: str = Field(default_factory=now)


# ---------------------------------------------------------------------------
# Quiz & answer models
# ---------------------------------------------------------------------------

QuizMode = Literal["topic", "random", "weakest", "exam"]


class Quiz(BaseModel):
    id: str = Field(default_factory=new_id)
    title: str = ""
    question_ids: list[str] = Field(default_factory=list)
    mode: QuizMode = "random"
    created_at: str = Field(default_factory=now)
    completed_at: str | None = None
    score: float | None = None
    total: int = 0  # number of questions


class Answer(BaseModel):
    quiz_id: str = ""
    question_id: str = ""
    question_type: QuestionType = "multiple_choice"
    question_text: str = ""
    correct_answer: str = ""
    user_answer: str = ""
    is_correct: bool | None = None
    feedback: str | None = None
    answered_at: str = Field(default_factory=now)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TopicStat(BaseModel):
    topic: str
    total_attempts: int = 0
    correct_count: int = 0
    last_attempted: str | None = None

    @property
    def accuracy(self) -> float:
        if self.total_attempts == 0:
            return 0.0
        return self.correct_count / self.total_attempts


class GlobalStats(BaseModel):
    total_quizzes: int = 0
    total_questions_answered: int = 0
    total_correct: int = 0
    topics: list[TopicStat] = Field(default_factory=list)

    @property
    def overall_accuracy(self) -> float:
        if self.total_questions_answered == 0:
            return 0.0
        return self.total_correct / self.total_questions_answered


# ---------------------------------------------------------------------------
# Shared constants & utilities
# ---------------------------------------------------------------------------

QUESTION_TYPE_MAP: dict[str, str] = {
    "mc": "multiple_choice",
    "tf": "true_false",
    "code": "programming",
    "short": "short_answer",
    "fill": "fill_blank",
}

ALL_QUESTION_TYPES: list[QuestionType] = [
    "multiple_choice", "true_false", "programming", "short_answer", "fill_blank"
]


def note_sections_to_dicts(note: Note) -> list[dict]:
    """Convert a Note's sections to dicts for storage/generation.

    Standardizes the section dict format used across storage, generator,
    CLI, and web app.
    """
    return [
        {
            "id": s.id,
            "note_path": note.path,
            "heading": s.heading,
            "level": s.level,
            "content": s.content,
            "code_blocks": s.code_blocks,
        }
        for s in note.sections
    ]
