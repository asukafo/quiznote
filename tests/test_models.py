"""Tests for data models."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from notedrill.models import (
    Option,
    Question,
    Quiz,
    Answer,
    Note,
    Section,
    TopicStat,
    GlobalStats,
    new_id,
    now,
)


class TestOption:
    def test_create_option(self):
        opt = Option(id="a", text="The answer")
        assert opt.id == "a"
        assert opt.text == "The answer"


class TestQuestion:
    def test_defaults(self):
        q = Question(
            type="multiple_choice",
            question="What is 2+2?",
            correct_answer="4",
            explanation="Basic arithmetic",
        )
        assert q.id  # auto-generated
        assert q.difficulty == "medium"
        assert q.created_at  # auto-generated

    def test_with_options(self):
        q = Question(
            type="multiple_choice",
            topic="math",
            question="What is 2+2?",
            options=[Option(id="a", text="3"), Option(id="b", text="4")],
            correct_answer="b",
            explanation="2+2=4",
        )
        assert len(q.options) == 2
        assert q.options[0].text == "3"

    def test_programming_question(self):
        q = Question(
            type="programming",
            topic="python",
            question="Fix the bug",
            code_context="def foo():\n    pass",
            correct_answer="Add return",
            explanation="Function needs return",
        )
        assert q.code_context is not None
        assert "def foo" in q.code_context


class TestQuiz:
    def test_create_quiz(self):
        quiz = Quiz(
            title="Test Quiz",
            question_ids=["abc", "def"],
            mode="random",
        )
        assert quiz.id
        assert quiz.total == 0  # default

    def test_quiz_with_score(self):
        quiz = Quiz(
            title="Test",
            question_ids=["a"],
            score=85.5,
            total=10,
        )
        assert quiz.score == 85.5


class TestAnswer:
    def test_create_answer(self):
        a = Answer(
            quiz_id="q1",
            question_id="q1_1",
            question_type="multiple_choice",
            question_text="What?",
            correct_answer="b",
            user_answer="b",
            is_correct=True,
        )
        assert a.answered_at


class TestNote:
    def test_create_note(self):
        note = Note(
            path="subdir/test.md",
            title="Test Note",
            tags=["python", "tutorial"],
            content="# Hello\nWorld",
        )
        assert note.path == "subdir/test.md"
        assert len(note.tags) == 2


class TestSection:
    def test_create_section(self):
        s = Section(
            heading="Introduction",
            level=2,
            content="Some content",
            code_blocks=[],
        )
        assert s.id
        assert s.heading == "Introduction"
        assert s.level == 2


class TestTopicStat:
    def test_accuracy_zero_when_no_attempts(self):
        ts = TopicStat(topic="test")
        assert ts.accuracy == 0.0

    def test_accuracy_perfect(self):
        ts = TopicStat(topic="test", total_attempts=10, correct_count=10)
        assert ts.accuracy == 1.0

    def test_accuracy_half(self):
        ts = TopicStat(topic="test", total_attempts=10, correct_count=5)
        assert ts.accuracy == 0.5


class TestGlobalStats:
    def test_overall_accuracy_zero_when_no_answers(self):
        gs = GlobalStats()
        assert gs.overall_accuracy == 0.0

    def test_overall_accuracy(self):
        gs = GlobalStats(
            total_quizzes=2,
            total_questions_answered=10,
            total_correct=7,
        )
        assert gs.overall_accuracy == 0.7


class TestNewID:
    def test_generates_unique_ids(self):
        ids = {new_id() for _ in range(100)}
        assert len(ids) == 100

    def test_id_length(self):
        assert len(new_id()) == 12


class TestNow:
    def test_returns_iso_format(self):
        t = now()
        assert "T" in t  # ISO 8601
        assert len(t) > 20
