# QuizNote

AI-powered quiz generator from your Obsidian notes. Select files/directories, generate questions via Claude Code, take quizzes with auto-grading.

## Setup

```bash
pip install -e .
quiznote init --vault /path/to/your/obsidian/vault
quiznote parse
quiznote serve
```

Open `http://localhost:8080`.

## Features

- **VS Code-style file tree** — browse your vault hierarchy
- **File/directory selection** — generate questions from specific notes or entire folders
- **Multiple question types** — multiple choice, true/false, programming, short answer, fill-in-blank
- **Claude-powered** — uses `claude -p` for generation and grading (no API key needed)
- **SQLite storage** — `.quiznote.db` lives inside your vault, travels with your notes
- **CRUD** — edit or delete questions via web UI or CLI

## CLI Commands

```bash
quiznote init --vault PATH    # Initialize
quiznote parse                # Parse all notes
quiznote generate -n 5 -t mc # Generate questions
quiznote take                 # Take a quiz (interactive)
quiznote review               # Review past quizzes
quiznote stats                # Learning statistics
quiznote question show <id>   # View question details
quiznote question edit <id>   # Edit a question
quiznote question delete <id>  # Delete a question
quiznote serve                # Start web server
```
