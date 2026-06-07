"""Quiz engine — manages quiz sessions and answer collection."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from .models import Answer, Question, Quiz, QuizMode, new_id, now

if TYPE_CHECKING:
    from .storage import Storage


class QuizSession:
    """An active quiz session. Holds state while user answers questions."""

    def __init__(
        self,
        storage: Storage,
        quiz: Quiz,
        questions: list[Question],
    ):
        self.storage = storage
        self.quiz = quiz
        self.questions = questions
        self._current_index = 0
        self._answers: list[Answer] = []

    @property
    def current_index(self) -> int:
        return self._current_index

    @property
    def current_question(self) -> Question | None:
        if self._current_index < len(self.questions):
            return self.questions[self._current_index]
        return None

    @property
    def total(self) -> int:
        return len(self.questions)

    @property
    def answered_count(self) -> int:
        return len(self._answers)

    @property
    def is_finished(self) -> bool:
        return self._current_index >= len(self.questions)

    @property
    def answers(self) -> list[Answer]:
        return self._answers

    def submit_answer(self, user_answer: str) -> Answer:
        """Record an answer for the current question and advance."""
        q = self.current_question
        if q is None:
            raise IndexError("No more questions in this quiz.")

        answer = Answer(
            quiz_id=self.quiz.id,
            question_id=q.id,
            question_type=q.type,
            question_text=q.question,
            correct_answer=q.correct_answer,
            user_answer=user_answer,
            answered_at=now(),
        )
        self._answers.append(answer)
        self._current_index += 1
        return answer

    def finish(self) -> Quiz:
        """Mark quiz as finished (grading happens separately)."""
        self.quiz.completed_at = now()
        self.quiz.total = len(self.questions)
        self.storage.save_quiz(self.quiz)
        return self.quiz


# ---------------------------------------------------------------------------
# Quiz factory
# ---------------------------------------------------------------------------

def create_quiz(
    storage: Storage,
    title: str = "",
    question_ids: list[str] | None = None,
    mode: QuizMode = "random",
    topic: str | None = None,
    count: int = 10,
) -> QuizSession:
    """Create a new quiz session.

    Args:
        storage: Storage backend.
        title: Quiz title.
        question_ids: Explicit question IDs (if provided, ignores topic/count).
        mode: Selection mode.
        topic: Topic filter (for 'topic' and 'weakest' modes).
        count: Number of questions (for 'random' and 'topic' modes).

    Returns:
        A QuizSession ready for answering.
    """
    if question_ids:
        # Use explicit question list
        selected_ids = question_ids
        if not title:
            title = f"Quiz {now()[:10]}"
    elif mode == "topic" and topic:
        questions = storage.list_questions(topic=topic, limit=count * 2)
        random.shuffle(questions)
        selected_ids = [q.id for q in questions[:count]]
        if not title:
            title = f"{topic} Quiz"
    elif mode == "weakest":
        weakest_topics = storage.get_weakest_topics(limit=3)
        if weakest_topics:
            weakest_qs: list[Question] = []
            for t in weakest_topics:
                qs = storage.list_questions(topic=t, limit=count // len(weakest_topics) + 1)
                weakest_qs.extend(qs)
            random.shuffle(weakest_qs)
            selected_ids = [q.id for q in weakest_qs[:count]]
        else:
            # Fallback to random if no weak topics
            questions = storage.list_questions(limit=count * 2)
            random.shuffle(questions)
            selected_ids = [q.id for q in questions[:count]]
        if not title:
            title = "Weakest Topics Review"
    elif mode == "exam":
        # Exam mode: mix of topics and difficulties
        questions = storage.list_questions(limit=count * 3)
        random.shuffle(questions)
        selected_ids = [q.id for q in questions[:count]]
        if not title:
            title = f"Exam {now()[:10]}"
    else:
        # random mode (default)
        questions = storage.list_questions(topic=topic, limit=count * 2)
        random.shuffle(questions)
        selected_ids = [q.id for q in questions[:count]]
        if not title:
            title = f"Random Quiz {now()[:10]}"

    if not selected_ids:
        raise ValueError("No questions available. Generate some questions first.")

    # Load full question objects
    question_objs = []
    for qid in selected_ids:
        q = storage.get_question(qid)
        if q:
            question_objs.append(q)

    if not question_objs:
        raise ValueError("No valid questions found.")

    quiz = Quiz(
        id=new_id(),
        title=title,
        question_ids=[q.id for q in question_objs],
        mode=mode,
        total=len(question_objs),
    )
    storage.save_quiz(quiz)

    return QuizSession(storage=storage, quiz=quiz, questions=question_objs)
