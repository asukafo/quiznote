"""SM-2 Spaced Repetition System for NoteDrill.

Based on the SuperMemo 2 algorithm by Piotr Wozniak.
Tracks review history per question and schedules optimal review dates.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Quality scale (0-5)
# 5 = perfect recall
# 4 = correct with hesitation
# 3 = correct with serious difficulty
# 2 = incorrect, but remembered when shown answer
# 1 = incorrect, vaguely remembered
# 0 = complete blackout


def sm2_quality(is_correct: bool, confidence: str = "medium") -> int:
    """Convert answer result to SM-2 quality score."""
    if is_correct:
        return {"high": 5, "medium": 4, "low": 3}.get(confidence, 4)
    else:
        return {"high": 2, "medium": 1, "low": 0}.get(confidence, 1)


def sm2_update(
    quality: int,
    repetitions: int,
    ease_factor: float,
    interval: float,
) -> tuple[int, float, float, str]:
    """SM-2 algorithm core.

    Args:
        quality: 0-5 rating of recall quality
        repetitions: consecutive correct reviews (resets on fail)
        ease_factor: current ease factor (≥ 1.3)
        interval: current interval in days

    Returns:
        (new_repetitions, new_ease_factor, new_interval, next_review_date)
    """
    if quality >= 3:
        # Successful recall
        if repetitions == 0:
            new_interval = 1.0
        elif repetitions == 1:
            new_interval = 6.0
        else:
            new_interval = round(interval * ease_factor, 1)
        new_repetitions = repetitions + 1
    else:
        # Failed recall — reset
        new_repetitions = 0
        new_interval = 1.0

    # Update ease factor
    new_ef = ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    new_ef = max(1.3, round(new_ef, 2))

    # Apply small fuzz to avoid card clustering (±5%)
    import random
    fuzz = random.uniform(0.95, 1.05)
    new_interval = round(new_interval * fuzz, 1)
    if new_interval < 1:
        new_interval = 1.0

    next_date = (datetime.now(timezone.utc) + timedelta(days=new_interval)).isoformat()

    return new_repetitions, new_ef, new_interval, next_date


def get_due_status(interval: float, next_review_at: str) -> str:
    """Determine card status based on interval and next review date."""
    if not next_review_at:
        return "new"

    next_dt = datetime.fromisoformat(next_review_at)
    now_dt = datetime.now(timezone.utc)

    if now_dt >= next_dt:
        return "due"  # Overdue — needs review now

    if interval < 1:
        return "learning"
    elif interval < 21:
        return "young"
    elif interval >= 21:
        return "mature"
    return "new"


def mastery_level(interval: float, repetitions: int) -> str:
    """Human-readable mastery level."""
    if repetitions == 0:
        return "🔴 初学"
    if interval < 7:
        return "🟠 入门"
    if interval < 21:
        return "🟡 巩固"
    if interval < 60:
        return "🟢 熟练"
    return "🔵 精通"
