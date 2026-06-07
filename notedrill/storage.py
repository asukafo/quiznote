"""SQLite storage layer for QuizNote."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .models import Answer, GlobalStats, Note, Question, Quiz, TopicStat


def _row_to_dict(row: tuple[str, ...], cols: list[str]) -> dict[str, Any]:
    return dict(zip(cols, row))


class Storage:
    """SQLite-backed storage for notes, questions, quizzes, answers, stats."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Init schema
    # ------------------------------------------------------------------

    def init_db(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS notes (
                path        TEXT PRIMARY KEY,
                title       TEXT NOT NULL DEFAULT '',
                tags        TEXT NOT NULL DEFAULT '[]',
                links       TEXT NOT NULL DEFAULT '[]',
                content     TEXT NOT NULL DEFAULT '',
                parsed_at   TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS sections (
                id            TEXT PRIMARY KEY,
                note_path     TEXT NOT NULL,
                heading       TEXT NOT NULL DEFAULT '',
                level         INTEGER NOT NULL DEFAULT 1,
                content       TEXT NOT NULL DEFAULT '',
                code_blocks_json TEXT NOT NULL DEFAULT '[]',
                question_ids_json TEXT NOT NULL DEFAULT '[]',
                FOREIGN KEY (note_path) REFERENCES notes(path) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS questions (
                id            TEXT PRIMARY KEY,
                type          TEXT NOT NULL DEFAULT 'multiple_choice',
                topic         TEXT NOT NULL DEFAULT '',
                difficulty    TEXT NOT NULL DEFAULT 'medium',
                question      TEXT NOT NULL DEFAULT '',
                options_json  TEXT,
                code_context  TEXT,
                correct_answer TEXT NOT NULL DEFAULT '',
                explanation   TEXT NOT NULL DEFAULT '',
                source_note   TEXT NOT NULL DEFAULT '',
                source_section TEXT NOT NULL DEFAULT '',
                created_at    TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS quizzes (
                id              TEXT PRIMARY KEY,
                title           TEXT NOT NULL DEFAULT '',
                question_ids_json TEXT NOT NULL DEFAULT '[]',
                mode            TEXT NOT NULL DEFAULT 'random',
                created_at      TEXT NOT NULL DEFAULT '',
                completed_at    TEXT,
                score           REAL,
                total           INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS answers (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                quiz_id         TEXT NOT NULL,
                question_id     TEXT NOT NULL,
                question_type   TEXT NOT NULL DEFAULT 'multiple_choice',
                question_text   TEXT NOT NULL DEFAULT '',
                correct_answer  TEXT NOT NULL DEFAULT '',
                user_answer     TEXT NOT NULL DEFAULT '',
                is_correct      INTEGER,
                feedback        TEXT,
                answered_at     TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS topic_stats (
                topic           TEXT PRIMARY KEY,
                total_attempts  INTEGER NOT NULL DEFAULT 0,
                correct_count   INTEGER NOT NULL DEFAULT 0,
                last_attempted  TEXT
            );

            CREATE TABLE IF NOT EXISTS srs_items (
                question_id     TEXT PRIMARY KEY,
                repetitions     INTEGER NOT NULL DEFAULT 0,
                ease_factor     REAL NOT NULL DEFAULT 2.5,
                interval_days   REAL NOT NULL DEFAULT 0.0,
                next_review_at  TEXT NOT NULL DEFAULT '',
                last_review_at  TEXT,
                lapses          INTEGER NOT NULL DEFAULT 0,
                total_reviews   INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS learning_sessions (
                id              TEXT PRIMARY KEY,
                mode            TEXT NOT NULL DEFAULT 'quiz',  -- quiz, present, deepdive
                file_paths_json TEXT NOT NULL DEFAULT '[]',
                notes_json      TEXT NOT NULL DEFAULT '{}',    -- session notes/results
                created_at      TEXT NOT NULL DEFAULT '',
                completed_at    TEXT
            );
        """)
        self.conn.commit()

    # ------------------------------------------------------------------
    # Notes
    # ------------------------------------------------------------------

    def save_note(self, note: Note) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO notes (path, title, tags, links, content, parsed_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (
                note.path,
                note.title,
                json.dumps(note.tags),
                json.dumps(note.links),
                note.content,
            ),
        )
        self.conn.commit()

    def get_note(self, path: str) -> Note | None:
        row = self.conn.execute("SELECT * FROM notes WHERE path = ?", (path,)).fetchone()
        if row is None:
            return None
        return Note(
            path=row["path"],
            title=row["title"],
            tags=json.loads(row["tags"]),
            links=json.loads(row["links"]),
            content=row["content"],
        )

    def list_notes(self) -> list[Note]:
        rows = self.conn.execute("SELECT * FROM notes ORDER BY title").fetchall()
        return [
            Note(
                path=r["path"],
                title=r["title"],
                tags=json.loads(r["tags"]),
                links=json.loads(r["links"]),
                content=r["content"],
            )
            for r in rows
        ]

    def get_all_topics(self) -> list[str]:
        """Return distinct topics from saved questions."""
        rows = self.conn.execute(
            "SELECT DISTINCT topic FROM questions WHERE topic != '' ORDER BY topic"
        ).fetchall()
        return [r["topic"] for r in rows]

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def save_sections(self, note_path: str, sections) -> None:
        """Save all sections for a note. Each section is a (id, heading, level, content, code_blocks) tuple."""
        # First delete old sections for this note
        self.conn.execute("DELETE FROM sections WHERE note_path = ?", (note_path,))
        for (sid, heading, level, content, code_blocks) in sections:
            self.conn.execute(
                """INSERT INTO sections (id, note_path, heading, level, content, code_blocks_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (sid, note_path, heading, level, content, json.dumps(code_blocks)),
            )
        self.conn.commit()

    def get_sections_for_note(self, note_path: str) -> list[dict]:
        """Return all sections for a note, with question count."""
        rows = self.conn.execute(
            """SELECT s.*,
               (SELECT COUNT(*) FROM questions q WHERE q.source_section = s.id) as question_count
               FROM sections s WHERE s.note_path = ? ORDER BY s.level, s.heading""",
            (note_path,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "note_path": r["note_path"],
                "heading": r["heading"],
                "level": r["level"],
                "content": r["content"],
                "code_blocks": json.loads(r["code_blocks_json"]),
                "question_ids": json.loads(r["question_ids_json"]),
                "question_count": r["question_count"],
            }
            for r in rows
        ]

    def get_sections_by_ids(self, section_ids: list[str]) -> list[dict]:
        """Return sections by their IDs."""
        if not section_ids:
            return []
        placeholders = ",".join("?" * len(section_ids))
        rows = self.conn.execute(
            f"SELECT * FROM sections WHERE id IN ({placeholders})",
            section_ids,
        ).fetchall()
        return [
            {
                "id": r["id"],
                "note_path": r["note_path"],
                "heading": r["heading"],
                "level": r["level"],
                "content": r["content"],
                "code_blocks": json.loads(r["code_blocks_json"]),
                "question_ids": json.loads(r["question_ids_json"]),
            }
            for r in rows
        ]

    def link_question_to_section(self, question_id: str, section_id: str) -> None:
        """Add question_id to a section's question_ids."""
        row = self.conn.execute(
            "SELECT question_ids_json FROM sections WHERE id = ?", (section_id,)
        ).fetchone()
        if row:
            ids = json.loads(row["question_ids_json"])
            if question_id not in ids:
                ids.append(question_id)
            self.conn.execute(
                "UPDATE sections SET question_ids_json = ? WHERE id = ?",
                (json.dumps(ids), section_id),
            )
            self.conn.commit()

    # ------------------------------------------------------------------
    # Questions
    # ------------------------------------------------------------------

    def save_question(self, q: Question) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO questions
               (id, type, topic, difficulty, question, options_json,
                code_context, correct_answer, explanation, source_note, source_section, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                q.id,
                q.type,
                q.topic,
                q.difficulty,
                q.question,
                json.dumps([o.model_dump() for o in q.options]) if q.options else None,
                q.code_context,
                q.correct_answer,
                q.explanation,
                q.source_note,
                q.source_section,
                q.created_at,
            ),
        )
        self.conn.commit()

    def save_questions(self, questions: list[Question]) -> None:
        for q in questions:
            self.save_question(q)

    def get_question(self, qid: str) -> Question | None:
        row = self.conn.execute(
            "SELECT * FROM questions WHERE id = ?", (qid,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_question(row)

    def list_questions(
        self,
        topic: str | None = None,
        qtype: str | None = None,
        difficulty: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Question]:
        query = "SELECT * FROM questions WHERE 1=1"
        params: list[Any] = []
        if topic:
            query += " AND topic = ?"
            params.append(topic)
        if qtype and qtype != "all":
            query += " AND type = ?"
            params.append(qtype)
        if difficulty and difficulty != "mixed":
            query += " AND difficulty = ?"
            params.append(difficulty)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self.conn.execute(query, params).fetchall()
        return [self._row_to_question(r) for r in rows]

    def count_questions(
        self, topic: str | None = None, qtype: str | None = None
    ) -> int:
        query = "SELECT COUNT(*) FROM questions WHERE 1=1"
        params: list[Any] = []
        if topic:
            query += " AND topic = ?"
            params.append(topic)
        if qtype and qtype != "all":
            query += " AND type = ?"
            params.append(qtype)
        row = self.conn.execute(query, params).fetchone()
        return row[0] if row else 0

    def update_question(self, qid: str, **fields) -> bool:
        """Update specific fields of a question. Returns True if found."""
        allowed = {"type", "topic", "difficulty", "question", "options_json",
                    "code_context", "correct_answer", "explanation"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [qid]

        self.conn.execute(
            f"UPDATE questions SET {set_clause} WHERE id = ?", values
        )
        self.conn.commit()
        return self.conn.total_changes > 0

    def delete_question(self, qid: str) -> None:
        self.conn.execute("DELETE FROM questions WHERE id = ?", (qid,))
        self.conn.commit()

    def _row_to_question(self, row: sqlite3.Row) -> Question:
        from .models import Option

        opts = None
        if row["options_json"]:
            opts = [Option(**o) for o in json.loads(row["options_json"])]
        return Question(
            id=row["id"],
            type=row["type"],
            topic=row["topic"],
            difficulty=row["difficulty"],
            question=row["question"],
            options=opts,
            code_context=row["code_context"],
            correct_answer=row["correct_answer"],
            explanation=row["explanation"],
            source_note=row["source_note"],
            source_section=row["source_section"] if "source_section" in row.keys() else "",
            created_at=row["created_at"],
        )

    # ------------------------------------------------------------------
    # Quizzes
    # ------------------------------------------------------------------

    def save_quiz(self, quiz: Quiz) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO quizzes
               (id, title, question_ids_json, mode, created_at, completed_at, score, total)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                quiz.id,
                quiz.title,
                json.dumps(quiz.question_ids),
                quiz.mode,
                quiz.created_at,
                quiz.completed_at,
                quiz.score,
                quiz.total,
            ),
        )
        self.conn.commit()

    def get_quiz(self, qid: str) -> Quiz | None:
        row = self.conn.execute(
            "SELECT * FROM quizzes WHERE id = ?", (qid,)
        ).fetchone()
        if row is None:
            return None
        return Quiz(
            id=row["id"],
            title=row["title"],
            question_ids=json.loads(row["question_ids_json"]),
            mode=row["mode"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            score=row["score"],
            total=row["total"],
        )

    def list_quizzes(self, limit: int = 50) -> list[Quiz]:
        rows = self.conn.execute(
            "SELECT * FROM quizzes ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            Quiz(
                id=r["id"],
                title=r["title"],
                question_ids=json.loads(r["question_ids_json"]),
                mode=r["mode"],
                created_at=r["created_at"],
                completed_at=r["completed_at"],
                score=r["score"],
                total=r["total"],
            )
            for r in rows
        ]

    def complete_quiz(self, quiz_id: str, score: float) -> None:
        self.conn.execute(
            "UPDATE quizzes SET completed_at = datetime('now'), score = ? WHERE id = ?",
            (score, quiz_id),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Answers
    # ------------------------------------------------------------------

    def save_answer(self, answer: Answer) -> None:
        self.conn.execute(
            """INSERT INTO answers
               (quiz_id, question_id, question_type, question_text,
                correct_answer, user_answer, is_correct, feedback, answered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                answer.quiz_id,
                answer.question_id,
                answer.question_type,
                answer.question_text,
                answer.correct_answer,
                answer.user_answer,
                1 if answer.is_correct else (0 if answer.is_correct is not None else None),
                answer.feedback,
                answer.answered_at,
            ),
        )
        self.conn.commit()

    def save_answers(self, answers: list[Answer]) -> None:
        for a in answers:
            self.save_answer(a)

    def get_answers_for_quiz(self, quiz_id: str) -> list[Answer]:
        rows = self.conn.execute(
            "SELECT * FROM answers WHERE quiz_id = ? ORDER BY answered_at",
            (quiz_id,),
        ).fetchall()
        return [
            Answer(
                quiz_id=r["quiz_id"],
                question_id=r["question_id"],
                question_type=r["question_type"],
                question_text=r["question_text"],
                correct_answer=r["correct_answer"],
                user_answer=r["user_answer"],
                is_correct=bool(r["is_correct"]) if r["is_correct"] is not None else None,
                feedback=r["feedback"],
                answered_at=r["answered_at"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def update_topic_stat(self, topic: str, correct: bool) -> None:
        self.conn.execute(
            """INSERT INTO topic_stats (topic, total_attempts, correct_count, last_attempted)
               VALUES (?, 1, ?, datetime('now'))
               ON CONFLICT(topic) DO UPDATE SET
                 total_attempts = total_attempts + 1,
                 correct_count = correct_count + ?,
                 last_attempted = datetime('now')""",
            (topic, 1 if correct else 0, 1 if correct else 0),
        )
        self.conn.commit()

    def get_stats(self) -> GlobalStats:
        # Aggregate from topic_stats
        rows = self.conn.execute("SELECT * FROM topic_stats ORDER BY topic").fetchall()
        topics = [
            TopicStat(
                topic=r["topic"],
                total_attempts=r["total_attempts"],
                correct_count=r["correct_count"],
                last_attempted=r["last_attempted"],
            )
            for r in rows
        ]
        quiz_count = self.conn.execute("SELECT COUNT(*) FROM quizzes").fetchone()[0]
        ans_row = self.conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) FROM answers"
        ).fetchone()
        return GlobalStats(
            total_quizzes=quiz_count,
            total_questions_answered=ans_row[0] or 0,
            total_correct=ans_row[1] or 0,
            topics=topics,
        )

    def get_weakest_topics(self, limit: int = 3) -> list[str]:
        """Return topics with lowest accuracy, for focused review."""
        rows = self.conn.execute(
            """SELECT topic FROM topic_stats
               WHERE total_attempts >= 3
               ORDER BY CAST(correct_count AS REAL) / total_attempts ASC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [r["topic"] for r in rows]

    # ------------------------------------------------------------------
    # SRS (Spaced Repetition)
    # ------------------------------------------------------------------

    def get_srs_item(self, question_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM srs_items WHERE question_id = ?", (question_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def upsert_srs(self, question_id: str, repetitions: int, ease_factor: float,
                   interval_days: float, next_review_at: str, is_lapse: bool = False) -> None:
        existing = self.get_srs_item(question_id)
        if existing:
            self.conn.execute(
                """UPDATE srs_items SET repetitions=?, ease_factor=?, interval_days=?,
                   next_review_at=?, last_review_at=datetime('now'),
                   lapses=lapses + ?, total_reviews=total_reviews+1
                   WHERE question_id=?""",
                (repetitions, ease_factor, interval_days, next_review_at,
                 1 if is_lapse else 0, question_id),
            )
        else:
            self.conn.execute(
                """INSERT INTO srs_items (question_id, repetitions, ease_factor,
                   interval_days, next_review_at, last_review_at, total_reviews)
                   VALUES (?, ?, ?, ?, ?, datetime('now'), 1)""",
                (question_id, repetitions, ease_factor, interval_days, next_review_at),
            )
        self.conn.commit()

    def get_due_srs_questions(self, limit: int = 20) -> list[str]:
        """Return question IDs due for review."""
        rows = self.conn.execute(
            """SELECT question_id FROM srs_items
               WHERE next_review_at <= datetime('now') OR next_review_at = ''
               ORDER BY next_review_at ASC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [r["question_id"] for r in rows]

    def get_srs_stats(self) -> dict:
        """Return SRS statistics."""
        total = self.conn.execute("SELECT COUNT(*) FROM srs_items").fetchone()[0]
        due = self.conn.execute(
            "SELECT COUNT(*) FROM srs_items WHERE next_review_at <= datetime('now') OR next_review_at = ''"
        ).fetchone()[0]
        mature = self.conn.execute(
            "SELECT COUNT(*) FROM srs_items WHERE interval_days >= 21"
        ).fetchone()[0]
        # Reviews completed today
        today = self.conn.execute(
            "SELECT COUNT(*) FROM srs_items WHERE date(last_review_at) = date('now')"
        ).fetchone()[0]
        return {
            "total": total,
            "due": due,
            "mature": mature,
            "reviewed_today": today,
        }

    def get_review_history(self, days: int = 30) -> list[dict]:
        """Return review count per day for heatmap."""
        rows = self.conn.execute(
            """SELECT date(last_review_at) as day, COUNT(*) as count
               FROM srs_items
               WHERE last_review_at >= date('now', ?)
               GROUP BY day ORDER BY day""",
            (f"-{days} days",),
        ).fetchall()
        return [{"day": r["day"], "count": r["count"]} for r in rows]

    # ------------------------------------------------------------------
    # Learning Sessions
    # ------------------------------------------------------------------

    def save_session(self, session_id: str, mode: str, file_paths: list[str],
                     notes: dict | None = None) -> None:
        import json
        self.conn.execute(
            """INSERT OR REPLACE INTO learning_sessions (id, mode, file_paths_json, notes_json, created_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (session_id, mode, json.dumps(file_paths), json.dumps(notes or {})),
        )
        self.conn.commit()

    def complete_session(self, session_id: str) -> None:
        self.conn.execute(
            "UPDATE learning_sessions SET completed_at = datetime('now') WHERE id = ?",
            (session_id,),
        )
        self.conn.commit()

    def get_sessions(self, limit: int = 20) -> list[dict]:
        import json
        rows = self.conn.execute(
            "SELECT * FROM learning_sessions ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            {**dict(r), "file_paths": json.loads(r["file_paths_json"]),
             "notes": json.loads(r["notes_json"])}
            for r in rows
        ]
