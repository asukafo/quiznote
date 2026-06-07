"""CLI entry point for NoteDrill — turn your notes into a learning drill ground."""

from __future__ import annotations

import os
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn

from .config import load_config, save_config, Config
from .storage import Storage
from .parser import parse_vault
from .generator import QuestionGenerator, QuestionCritic, sections_to_text
from .quiz import create_quiz, QuizSession
from .grader import grade_answer, compute_score, AIGrader
from .models import QuestionType, QUESTION_TYPE_MAP, ALL_QUESTION_TYPES, note_sections_to_dicts

app = typer.Typer(
    name="notedrill",
    help="Turn your Obsidian notes into a personal learning drill ground.",
    no_args_is_help=True,
)

console = Console()
_config: Config | None = None
_storage: Storage | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        cfg = get_config()
        _storage = Storage(cfg.resolve_db_path())
        _storage.init_db()
        _storage.migrate()
    return _storage


def get_generator() -> QuestionGenerator:
    cfg = get_config()
    return QuestionGenerator(model=cfg.anthropic_model)


def get_ai_grader() -> AIGrader:
    cfg = get_config()
    return AIGrader(model=cfg.anthropic_model)


def _print_critic_summary(summary: dict) -> None:
    """Print critic review summary with colored output."""
    if not summary:
        return
    total = summary.get("total", 0)
    accepted = summary.get("accepted", 0)
    revised = summary.get("revised", 0)
    rejected = summary.get("rejected", 0)
    assessment = summary.get("overall_assessment", "")

    console.print()
    panel_title = "Critic Review Summary"
    lines = [
        f"Total reviewed: {total}",
        f"[green]Accepted: {accepted}[/green]",
        f"[yellow]Revised: {revised}[/yellow]",
        f"[red]Rejected: {rejected}[/red]",
    ]
    if assessment:
        lines.append(f"\n[dim]{assessment}[/dim]")
    console.print(Panel("\n".join(lines), title=panel_title))


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@app.command()
def init(
    vault_path: str = typer.Option(
        "notes", "--vault", "-v", help="Path to your Obsidian vault (or notes directory)"
    ),
):
    """Initialize NoteDrill with vault path. Uses Claude Code CLI for AI features."""
    cfg = Config()

    # Resolve vault path
    if os.path.isabs(vault_path):
        cfg.vault_path = vault_path
    else:
        cfg.vault_path = os.path.abspath(vault_path)

    cfg.anthropic_api_key = ""  # Not needed — uses Claude Code CLI
    save_config(cfg)

    # Ensure vault directory exists
    os.makedirs(cfg.vault_path, exist_ok=True)

    # Init database
    storage = Storage(cfg.resolve_db_path())
    storage.init_db()
    storage.migrate()

    console.print(f"[green]✓[/green] NoteDrill initialized!")
    console.print(f"  Vault: {cfg.vault_path}")
    console.print(f"  Database: {cfg.resolve_db_path()}")
    console.print(f"  Config: ~/.notedrill/config.toml")
    console.print()
    console.print("Drop your Obsidian .md notes into the vault, then run:")
    console.print("  [bold]notedrill generate[/bold]")


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------

@app.command()
def parse():
    """Parse all notes in the vault and show a summary."""
    cfg = get_config()
    storage = get_storage()

    console.print(f"[dim]Parsing vault: {cfg.vault_path}[/dim]")
    notes = parse_vault(cfg.vault_path)

    table = Table(title="Parsed Notes", show_lines=True)
    table.add_column("Title", style="cyan")
    table.add_column("Path")
    table.add_column("Tags", style="yellow")
    table.add_column("Sections")
    table.add_column("Links")

    for note in notes:
        storage.save_note(note)
        # Also save sections
        section_tuples = [
            (s.id, s.heading, s.level, s.content, s.code_blocks)
            for s in note.sections
        ]
        storage.save_sections(note.path, section_tuples)
        table.add_row(
            note.title,
            note.path,
            ", ".join(note.tags[:5]),
            str(len(note.sections)),
            str(len(note.links)),
        )

    console.print(table)
    console.print(f"[green]✓[/green] Parsed and indexed {len(notes)} notes.")


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------

@app.command()
def generate(
    count: int = typer.Option(10, "--count", "-n", help="Number of questions to generate"),
    qtype: str = typer.Option(
        "all", "--type", "-t", help="Question types: mc, tf, code, short, fill, all"
    ),
    difficulty: str = typer.Option(
        "mixed", "--difficulty", "-d", help="Difficulty: easy, medium, hard, mixed"
    ),
    topic: Optional[str] = typer.Option(None, "--topic", help="Topic filter"),
    no_critic: bool = typer.Option(False, "--no-critic", help="Skip the critic review step"),
):
    """Generate quiz questions from your notes using AI, with critic review."""
    cfg = get_config()
    storage = get_storage()

    # Parse notes first
    console.print("[dim]Parsing notes...[/dim]")
    notes = parse_vault(cfg.vault_path)
    for note in notes:
        storage.save_note(note)

    if not notes:
        console.print("[red]No notes found in vault.[/red] Drop some .md files and try again.")
        raise typer.Exit(1)

    console.print(f"Found {len(notes)} notes. Generating questions...")

    # Map type shorthand
    if qtype == "all":
        question_types = list(ALL_QUESTION_TYPES)
    else:
        question_types = []
        for t in qtype.split(","):
            t = t.strip()
            if t in QUESTION_TYPE_MAP:
                question_types.append(QUESTION_TYPE_MAP[t])  # type: ignore
            elif t in ALL_QUESTION_TYPES:
                question_types.append(t)  # type: ignore

    if not question_types:
        console.print(f"[red]Unknown question type: {qtype}[/red]")
        raise typer.Exit(1)

    try:
        generator = get_generator()
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error initializing generator: {e}[/red]")
        raise typer.Exit(1)

    last_msg = [""]
    def _show_progress(phase: str, msg: str):
        # Show last streaming message from Claude CLI
        if phase == "claude":
            last_msg[0] = msg[:100]
            console.print(f"  [dim]{last_msg[0]}[/dim]", end="\r")
        elif phase == "parse" and last_msg[0]:
            console.print()  # newline after streaming

    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
    ) as progress:
        task = progress.add_task("Generating questions via Claude Code...", total=None)
        try:
            # Use sections-based generation with progress
            sections: list[dict] = []
            for note in notes:
                sections.extend(note_sections_to_dicts(note))
            questions = generator.generate_batch_from_sections(
                sections, count=count, question_types=question_types,
                difficulty=difficulty, topic=topic,
                progress_callback=_show_progress,
            )
        except Exception:
            # Fallback to legacy
            questions = generator.generate_batch(
                notes, count=count, question_types=question_types, difficulty=difficulty, topic=topic
            )
        progress.update(task, completed=True)

    # ── Critic review step ──
    critic_summary = None
    if not no_critic and questions:
        console.print()
        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
        ) as progress:
            task = progress.add_task(f"Critic reviewing {len(questions)} questions...", total=None)
            critic = QuestionCritic(model=cfg.anthropic_model)

            # Build sections text for critic
            sections: list[dict] = []
            for note in notes:
                sections.extend(note_sections_to_dicts(note))
            sections_text = sections_to_text(sections)
            questions, critic_summary = critic.review(questions, sections_text)
            progress.update(task, completed=True)

        # Show critic results
        _print_critic_summary(critic_summary)

    storage.save_questions(questions)

    # Show summary
    table = Table(title=f"Generated {len(questions)} Questions")
    table.add_column("ID", style="dim")
    table.add_column("Type")
    table.add_column("Topic", style="cyan")
    table.add_column("Difficulty")
    table.add_column("Question (preview)")

    for q in questions:
        table.add_row(
            q.id[:8],
            q.type,
            q.topic,
            q.difficulty,
            q.question[:80] + ("..." if len(q.question) > 80 else ""),
        )

    console.print(table)
    console.print(f"[green]✓[/green] Saved {len(questions)} questions to database.")


# ---------------------------------------------------------------------------
# take
# ---------------------------------------------------------------------------

@app.command()
def take(
    quiz_id: Optional[str] = typer.Option(None, "--quiz-id", "-q", help="Take a specific quiz by ID"),
    topic: Optional[str] = typer.Option(None, "--topic", help="Topic filter for random quiz"),
    count: int = typer.Option(10, "--count", "-n", help="Number of questions"),
    mode: str = typer.Option("random", "--mode", "-m", help="Mode: random, topic, weakest, exam"),
):
    """Take a quiz — answer questions in the terminal."""
    storage = get_storage()

    # Create quiz session
    try:
        if quiz_id:
            quiz = storage.get_quiz(quiz_id)
            if quiz is None:
                console.print(f"[red]Quiz not found: {quiz_id}[/red]")
                raise typer.Exit(1)
            questions = []
            for qid in quiz.question_ids:
                q = storage.get_question(qid)
                if q:
                    questions.append(q)
            session = QuizSession(storage, quiz, questions)
        else:
            session = create_quiz(
                storage, mode=mode, topic=topic, count=count
            )
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    console.print(
        Panel.fit(
            f"[bold]{session.quiz.title}[/bold]\n"
            f"{session.total} questions | Mode: {session.quiz.mode}",
            title="Quiz",
        )
    )

    # Answer each question
    ai_grader = None
    try:
        ai_grader = get_ai_grader()
    except typer.Exit:
        pass  # AI grading optional

    while not session.is_finished:
        q = session.current_question
        if q is None:
            break

        idx = session.current_index + 1

        # Display question
        console.print()
        console.rule(f"Question {idx}/{session.total}")
        console.print(f"[bold cyan]{q.type.upper()}[/bold cyan] | {q.difficulty} | {q.topic}")
        console.print()
        console.print(Markdown(q.question))

        if q.code_context:
            console.print()
            console.print(Panel(q.code_context, title="Code", border_style="dim"))

        if q.options:
            for opt in q.options:
                console.print(f"  {opt.id}) {opt.text}")

        # Collect answer based on question type
        if q.type == "multiple_choice" and q.options:
            valid_opts = [o.id for o in q.options]
            answer = Prompt.ask(
                "\nYour answer", choices=valid_opts, show_choices=False
            )
        elif q.type == "true_false":
            answer = Prompt.ask(
                "\nYour answer", choices=["正确", "错误", "true", "false"], default="正确"
            )
        else:
            console.print("\n[dim]Enter your answer (press Enter twice to finish):[/dim]")
            lines = []
            while True:
                line = input()
                if line == "" and lines:
                    break
                lines.append(line)
            answer = "\n".join(lines)

        # Grade immediately
        answer_obj = grade_answer(q, answer, ai_grader)
        session.submit_answer(answer)

        if answer_obj.is_correct:
            console.print(f"[green]{answer_obj.feedback}[/green]")
        else:
            console.print(f"[red]{answer_obj.feedback}[/red]")

    # Finish quiz
    session.finish()

    # Save answers
    storage.save_answers(session.answers)
    score = compute_score(session.answers)

    # Update topic stats
    for ans, q in zip(session.answers, session.questions):
        if ans.is_correct is not None:
            storage.update_topic_stat(q.topic, ans.is_correct)

    storage.complete_quiz(session.quiz.id, score)

    # Show final score
    correct = sum(1 for a in session.answers if a.is_correct)
    console.print()
    color = "green" if score >= 80 else ("yellow" if score >= 60 else "red")
    console.print(
        Panel.fit(
            f"[bold {color}]Score: {score}%[/bold {color}]  ({correct}/{session.total} correct)",
            title="Quiz Complete",
        )
    )


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------

@app.command()
def review(
    quiz_id: Optional[str] = typer.Argument(None, help="Quiz ID to review"),
):
    """Review past quiz results."""
    storage = get_storage()

    if quiz_id:
        _review_quiz(storage, quiz_id)
    else:
        _list_quizzes(storage)


def _review_quiz(storage: Storage, quiz_id: str):
    quiz = storage.get_quiz(quiz_id)
    if quiz is None:
        console.print(f"[red]Quiz not found: {quiz_id}[/red]")
        raise typer.Exit(1)

    answers = storage.get_answers_for_quiz(quiz_id)

    console.print(Panel(f"[bold]{quiz.title}[/bold]", title="Quiz Review"))
    console.print(f"Mode: {quiz.mode} | Score: {quiz.score}% | Date: {quiz.created_at[:10]}\n")

    for i, ans in enumerate(answers, 1):
        icon = "✓" if ans.is_correct else "✗"
        color = "green" if ans.is_correct else "red"
        console.print(f"[{color}]{icon} Q{i}: {ans.question_text[:100]}[/{color}]")
        if ans.feedback:
            console.print(f"  [dim]{ans.feedback}[/dim]")
        console.print()


def _list_quizzes(storage: Storage):
    quizzes = storage.list_quizzes()
    if not quizzes:
        console.print("[dim]No quizzes taken yet. Run [bold]notedrill take[/bold].</dim]")
        return

    table = Table(title="Quiz History")
    table.add_column("ID", style="dim")
    table.add_column("Title")
    table.add_column("Mode")
    table.add_column("Score", justify="right")
    table.add_column("Date")

    for q in quizzes:
        score_str = f"{q.score}%" if q.score is not None else "-"
        color = "green" if (q.score or 0) >= 80 else ("yellow" if (q.score or 0) >= 60 else "red")
        table.add_row(
            q.id[:8],
            q.title,
            q.mode,
            f"[{color}]{score_str}[/{color}]",
            q.created_at[:10],
        )

    console.print(table)
    console.print("\nRun [bold]notedrill review <id>[/bold] to see full details.")


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

@app.command()
def stats():
    """Show learning statistics."""
    storage = get_storage()
    s = storage.get_stats()

    # Overall
    console.print(Panel(
        f"Total Quizzes: {s.total_quizzes}\n"
        f"Questions Answered: {s.total_questions_answered}\n"
        f"Overall Accuracy: [bold]{s.overall_accuracy:.1%}[/bold]",
        title="Overall Stats"
    ))

    # Per topic
    if s.topics:
        table = Table(title="Topic Breakdown")
        table.add_column("Topic", style="cyan")
        table.add_column("Attempts", justify="right")
        table.add_column("Accuracy", justify="right")
        table.add_column("Last Attempted")

        for t in s.topics:
            acc_color = "green" if t.accuracy >= 0.8 else ("yellow" if t.accuracy >= 0.6 else "red")
            table.add_row(
                t.topic,
                str(t.total_attempts),
                f"[{acc_color}]{t.accuracy:.1%}[/{acc_color}]",
                t.last_attempted[:10] if t.last_attempted else "-",
            )

        console.print(table)
    else:
        console.print("[dim]No topic stats yet. Take a quiz to build your stats![/dim]")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@app.command()
def list_cmd(
    resource: str = typer.Argument("topics", help="What to list: topics, notes, questions, quizzes"),
    topic: Optional[str] = typer.Option(None, "--topic", "-t", help="Filter by topic"),
    qtype: Optional[str] = typer.Option(None, "--type", help="Filter by question type"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max items to show"),
):
    """List resources (topics, notes, questions, quizzes)."""
    storage = get_storage()

    if resource == "topics":
        topics = storage.get_all_topics()
        if topics:
            console.print("[bold]Available Topics:[/bold]")
            for t in topics:
                count = storage.count_questions(topic=t)
                console.print(f"  • {t} ({count} questions)")
        else:
            console.print("[dim]No topics yet. Generate some questions first.[/dim]")

    elif resource == "notes":
        notes = storage.list_notes()
        if notes:
            table = Table(title="Notes")
            table.add_column("Title", style="cyan")
            table.add_column("Path")
            table.add_column("Tags")
            for n in notes:
                table.add_row(n.title, n.path, ", ".join(n.tags[:5]))
            console.print(table)
        else:
            console.print("[dim]No notes indexed. Run [bold]notedrill parse[/bold].</dim>")

    elif resource in ("questions", "q"):
        questions = storage.list_questions(topic=topic, qtype=qtype, limit=limit)
        if questions:
            table = Table(title="Questions")
            table.add_column("ID", style="dim")
            table.add_column("Type")
            table.add_column("Topic", style="cyan")
            table.add_column("Question")
            for q in questions:
                table.add_row(q.id[:8], q.type, q.topic, q.question[:80])
            console.print(table)
        else:
            console.print("[dim]No questions found.[/dim]")

    elif resource == "quizzes":
        _list_quizzes(storage)

    else:
        console.print(f"[red]Unknown resource: {resource}[/red]")
        console.print("Available: topics, notes, questions, quizzes")


# ---------------------------------------------------------------------------
# question CRUD
# ---------------------------------------------------------------------------

_question_app = typer.Typer(help="Manage questions", no_args_is_help=True)
app.add_typer(_question_app, name="question")


@_question_app.command("delete")
def question_delete(
    question_id: str = typer.Argument(..., help="Question ID to delete"),
):
    """Delete a question by ID."""
    storage = get_storage()
    q = storage.get_question(question_id)
    if q is None:
        console.print(f"[red]Question not found: {question_id}[/red]")
        raise typer.Exit(1)

    storage.delete_question(question_id)
    console.print(f"[green]✓[/green] Deleted: {q.question[:80]}")


@_question_app.command("edit")
def question_edit(
    question_id: str = typer.Argument(..., help="Question ID to edit"),
    question: Optional[str] = typer.Option(None, "--question", "-q", help="New question text"),
    answer: Optional[str] = typer.Option(None, "--answer", "-a", help="New correct answer"),
    explanation: Optional[str] = typer.Option(None, "--explanation", "-e", help="New explanation"),
    difficulty: Optional[str] = typer.Option(None, "--difficulty", "-d", help="New difficulty"),
    topic: Optional[str] = typer.Option(None, "--topic", "-t", help="New topic"),
):
    """Edit a question's fields."""
    storage = get_storage()
    q = storage.get_question(question_id)
    if q is None:
        console.print(f"[red]Question not found: {question_id}[/red]")
        raise typer.Exit(1)

    fields = {}
    if question is not None:
        fields["question"] = question
    if answer is not None:
        fields["correct_answer"] = answer
    if explanation is not None:
        fields["explanation"] = explanation
    if difficulty is not None:
        fields["difficulty"] = difficulty
    if topic is not None:
        fields["topic"] = topic

    if not fields:
        console.print("[yellow]No fields to update.[/yellow]")
        console.print(f"Current: type={q.type} topic={q.topic} difficulty={q.difficulty}")
        console.print(f"Question: {q.question}")
        console.print(f"Answer: {q.correct_answer}")
        console.print(f"Explanation: {q.explanation}")
        raise typer.Exit(0)

    storage.update_question(question_id, **fields)
    console.print(f"[green]✓[/green] Updated question {question_id[:8]}")


@_question_app.command("show")
def question_show(
    question_id: str = typer.Argument(..., help="Question ID to show"),
):
    """Show full details of a question."""
    storage = get_storage()
    q = storage.get_question(question_id)
    if q is None:
        console.print(f"[red]Question not found: {question_id}[/red]")
        raise typer.Exit(1)

    console.print(Panel(
        f"[bold cyan]{q.type}[/bold cyan] | {q.difficulty} | {q.topic}\n\n"
        f"{q.question}\n\n"
        + (f"Options:\n" + "\n".join(f"  {o.id}) {o.text}" for o in q.options) + "\n\n" if q.options else "")
        + f"[green]Answer: {q.correct_answer}[/green]\n"
        + f"[dim]Explanation: {q.explanation}[/dim]\n\n"
        + f"Source: {q.source_note} | ID: {q.id}",
        title="Question Detail"
    ))


# ---------------------------------------------------------------------------
# tree — show vault hierarchy
# ---------------------------------------------------------------------------

@app.command()
def tree(
    path: Optional[str] = typer.Argument(None, help="Show subtree starting from a specific path"),
):
    """Show the vault directory tree with note titles."""
    from .parser import list_vault_tree, _read_title

    cfg = get_config()
    vault = cfg.vault_path
    tree_data = list_vault_tree(vault)

    def _print_node(node: dict, indent: int = 0, prefix: str = ""):
        is_last = False  # simplified
        connector = "├── " if indent > 0 else ""
        if node["type"] == "directory":
            console.print(f"{'  ' * indent}{connector}📁 [bold cyan]{node['name']}[/bold cyan]")
            for i, child in enumerate(node.get("children", [])):
                _print_node(child, indent + 1)
        else:
            title = node.get("title", node["name"])
            console.print(f"{'  ' * indent}{connector}📄 [dim]{title}[/dim] [dim italic]({node['path']})[/dim italic]")

    console.print(f"[bold]📁 {tree_data['name']}[/bold]")
    for child in tree_data.get("children", []):
        _print_node(child, 0)


# ---------------------------------------------------------------------------
# pregenerate — bulk generate questions and save to vault
# ---------------------------------------------------------------------------

@app.command()
def pregenerate(
    count: int = typer.Option(10, "--count", "-n", help="Questions per batch"),
    qtype: str = typer.Option("all", "--type", "-t", help="mc, tf, code, short, fill, all"),
    difficulty: str = typer.Option("mixed", "--difficulty", "-d", help="easy, medium, hard, mixed"),
    topic: Optional[str] = typer.Option(None, "--topic", help="Topic filter"),
    path: Optional[str] = typer.Option(None, "--path", "-p", help="Only generate from this file or directory"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be generated without actually doing it"),
    no_critic: bool = typer.Option(False, "--no-critic", help="Skip the critic review step"),
):
    """Pre-generate questions from all notes and save to vault DB.

    This scans the entire vault (or a subset via --path) and generates
    questions for every markdown file, saving them into the .notedrill.db.
    After generation, a critic agent reviews all questions for quality.
    """
    cfg = get_config()
    storage = get_storage()

    # Parse
    console.print("[dim]Parsing vault...[/dim]")
    notes = parse_vault(cfg.vault_path)
    for note in notes:
        storage.save_note(note)

    # Filter by path
    if path:
        notes = [n for n in notes if n.path.startswith(path) or path in n.path]
        if not notes:
            console.print(f"[red]No notes found matching path: {path}[/red]")
            raise typer.Exit(1)

    console.print(f"Found [bold]{len(notes)}[/bold] notes to generate questions for.")
    console.print(f"Settings: {count} questions/batch, {qtype}, {difficulty}")
    if not no_critic:
        console.print("[dim]Critic review: enabled[/dim]")

    # Map types
    if qtype == "all":
        question_types = list(ALL_QUESTION_TYPES)
    else:
        question_types = [QUESTION_TYPE_MAP.get(t.strip(), "multiple_choice") for t in qtype.split(",")]  # type: ignore

    if dry_run:
        for note in notes:
            console.print(f"  [dim]Would generate {count} questions from: {note.title} ({note.path})[/dim]")
        console.print(f"[yellow]Dry run — {len(notes)} notes, ~{len(notes) * count} questions would be generated.[/yellow]")
        raise typer.Exit(0)

    try:
        generator = get_generator()
    except typer.Exit:
        raise

    # Build sections list once for critic reuse
    all_sections: list[dict] = []
    for note in notes:
        all_sections.extend(note_sections_to_dicts(note))
    all_sections_text = sections_to_text(all_sections)

    # Process in batches of 5 notes
    total_questions = 0
    total_accepted = 0
    total_revised = 0
    total_rejected = 0
    batch_size = 5
    critic = QuestionCritic(model=cfg.anthropic_model) if not no_critic else None

    for i in range(0, len(notes), batch_size):
        batch = notes[i:i + batch_size]
        batch_count = max(1, count * len(batch) // len(notes)) if len(notes) > 5 else count

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            task = progress.add_task(
                f"Generating from {len(batch)} notes ({i+1}-{min(i+batch_size, len(notes))}/{len(notes)})...",
                total=None
            )
            try:
                questions = generator.generate(
                    batch, count=batch_count,
                    question_types=question_types, difficulty=difficulty, topic=topic
                )
            except Exception as e:
                console.print(f"[red]Error on batch {i}: {e}[/red]")
                continue
            progress.update(task, completed=True)

        # ── Critic review for this batch ──
        if critic and questions:
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
                task = progress.add_task(f"Critic reviewing {len(questions)} questions...", total=None)
                try:
                    questions, c_summary = critic.review(questions, all_sections_text)
                    total_accepted += c_summary.get("accepted", 0)
                    total_revised += c_summary.get("revised", 0)
                    total_rejected += c_summary.get("rejected", 0)
                except Exception as e:
                    console.print(f"[yellow]Critic skipped (error): {e}[/yellow]")
                progress.update(task, completed=True)

        storage.save_questions(questions)
        total_questions += len(questions)
        for note in batch:
            console.print(f"  [green]✓[/green] {note.title}: {len(questions)} questions")

    console.print(f"\n[green]✓[/green] Total: [bold]{total_questions}[/bold] questions saved to vault DB.")
    if not no_critic:
        console.print(f"  Critic: {total_accepted} accepted, {total_revised} revised, {total_rejected} rejected")


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------

@app.command()
def serve(
    port: int = typer.Option(8080, "--port", "-p", help="Port to listen on"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
    public: bool = typer.Option(False, "--public", help="Allow LAN access (binds to 0.0.0.0)"),
):
    """Start the web interface."""
    import uvicorn
    from .app import create_app

    if public:
        host = "0.0.0.0"
        console.print("[yellow]⚠ Public mode: server accessible from LAN.[/yellow]")

    # Ensure db is initialized
    get_storage()

    web_app = create_app()
    console.print(f"[green]NoteDrill Web[/green] → http://{host}:{port}")
    uvicorn.run(web_app, host=host, port=port, log_level="info")


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------

def main():
    app()


if __name__ == "__main__":
    main()
