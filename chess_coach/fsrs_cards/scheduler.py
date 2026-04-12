"""FSRS v6 scheduler, wrapped around the ``fsrs`` Python package.

The package gives us a pure ``Scheduler`` + ``Card`` object model. We
convert back and forth between those objects and rows in the ``flashcards``
table. Reviews are logged into ``flashcard_reviews`` so we can feed them to
``fsrs_optimizer`` later for personalised parameters.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fsrs import Card, Rating, Scheduler, State

from .. import db


@dataclass
class FSRSState:
    state: int
    step: int
    stability: float
    difficulty: float
    due: datetime
    last_review: datetime | None
    scheduled_days: int
    reps: int
    lapses: int


_scheduler = Scheduler(
    desired_retention=0.9,
    learning_steps=(60, 600),
    relearning_steps=(600,),
    maximum_interval=36_500,
    enable_fuzzing=True,
)


def _card_from_row(row: dict[str, Any]) -> Card:
    """Rebuild an fsrs ``Card`` from a flashcard DB row."""
    return Card(
        card_id=int(row["id"]),
        state=State(row["state"]),
        step=row["step"],
        stability=row["stability"],
        difficulty=row["difficulty"],
        due=row["due"],
        last_review=row["last_review"],
    )


def _state_from_card(card: Card) -> FSRSState:
    return FSRSState(
        state=card.state.value,
        step=card.step or 0,
        stability=card.stability or 0.0,
        difficulty=card.difficulty or 0.0,
        due=card.due,
        last_review=card.last_review,
        scheduled_days=(card.due - (card.last_review or card.due)).days,
        reps=0,
        lapses=0,
    )


async def create_card(
    *,
    user_id: int,
    opening_node_id: int | None,
    fen: str | None,
    card_type: str,
    front_text: str,
    back_text: str,
    hint: str | None = None,
    tags: list[str] | None = None,
    source_model: str | None = None,
) -> int:
    card = Card()  # state = New
    row = await db.fetchrow(
        """
        INSERT INTO flashcards (
            user_id, opening_node_id, fen, card_type,
            front_text, back_text, hint, tags,
            state, step, stability, difficulty, due,
            generated_by, source_model
        ) VALUES (
            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,'llm',$14
        )
        RETURNING id
        """,
        user_id,
        opening_node_id,
        fen,
        card_type,
        front_text,
        back_text,
        hint,
        tags or [],
        card.state.value,
        card.step or 0,
        card.stability or 0.0,
        card.difficulty or 0.0,
        card.due,
        source_model,
    )
    return int(row["id"])


async def review_card(
    card_id: int,
    rating_int: int,
    *,
    user_id: int,
    review_duration_ms: int | None = None,
) -> FSRSState:
    """Apply a user rating (1..4) and persist the updated FSRS state."""
    row = await db.fetchrow(
        "SELECT * FROM flashcards WHERE id = $1 AND user_id = $2",
        card_id,
        user_id,
    )
    if row is None:
        raise LookupError(f"Flashcard {card_id} not found for user {user_id}")

    card = _card_from_row(dict(row))
    state_before = card.state.value
    stability_before = card.stability
    difficulty_before = card.difficulty
    elapsed_days = (
        (datetime.now(timezone.utc) - card.last_review).total_seconds() / 86_400.0
        if card.last_review else 0.0
    )

    rating = Rating(rating_int)
    new_card, review_log = _scheduler.review_card(
        card, rating, datetime.now(timezone.utc)
    )

    lapses = int(row["lapses"]) + (1 if rating_int == 1 else 0)
    reps = int(row["reps"]) + 1
    scheduled_days = (
        (new_card.due - (new_card.last_review or new_card.due)).days
    )

    async with db.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE flashcards SET
                    state = $1, step = $2, stability = $3, difficulty = $4,
                    due = $5, last_review = $6, scheduled_days = $7,
                    reps = $8, lapses = $9, updated_at = NOW()
                WHERE id = $10
                """,
                new_card.state.value,
                new_card.step or 0,
                new_card.stability,
                new_card.difficulty,
                new_card.due,
                new_card.last_review,
                scheduled_days,
                reps,
                lapses,
                card_id,
            )
            await conn.execute(
                """
                INSERT INTO flashcard_reviews (
                    flashcard_id, user_id, rating, state_before,
                    stability_before, difficulty_before, elapsed_days,
                    review_duration_ms, reviewed_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                """,
                card_id,
                user_id,
                rating_int,
                state_before,
                stability_before,
                difficulty_before,
                elapsed_days,
                review_duration_ms,
                datetime.now(timezone.utc),
            )

    return FSRSState(
        state=new_card.state.value,
        step=new_card.step or 0,
        stability=new_card.stability or 0.0,
        difficulty=new_card.difficulty or 0.0,
        due=new_card.due,
        last_review=new_card.last_review,
        scheduled_days=scheduled_days,
        reps=reps,
        lapses=lapses,
    )


async def get_due_cards(user_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """
        SELECT id, opening_node_id, fen, card_type, front_text, back_text,
               hint, tags, state, stability, difficulty, due
        FROM flashcards
        WHERE user_id = $1 AND due <= NOW()
        ORDER BY due ASC
        LIMIT $2
        """,
        user_id,
        limit,
    )
    return [dict(r) for r in rows]
