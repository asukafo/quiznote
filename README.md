# NoteDrill

Turn your Obsidian notes into a personal learning drill ground. Four modes, one vault.

## Modes

| Mode | What it does |
|------|-------------|
| 📝 **Quiz** | AI generates questions, you answer, SM-2 spaces reviews |
| 🎤 **Present** | AI builds a knowledge outline, you fill it in, AI reviews |
| 🔬 **Deep Dive** | Progressive follow-up questions drill deeper layer by layer |
| 🔄 **Review** | SM-2 spaced repetition — only cards due today |

## Setup

```bash
pip install -e .
notedrill init --vault /path/to/your/obsidian/vault
notedrill parse
notedrill serve
```

Open `http://localhost:8080`.

## CLI Commands

```bash
notedrill init --vault PATH     # Initialize
notedrill parse                 # Parse all notes
notedrill generate -n 5 -t mc  # Generate questions
notedrill take                  # Take a quiz (interactive)
notedrill review                # Review past quizzes
notedrill stats                 # Learning statistics
notedrill question show <id>    # View question details
notedrill question edit <id>    # Edit a question
notedrill question delete <id>  # Delete a question
notedrill serve                 # Start web server
```
