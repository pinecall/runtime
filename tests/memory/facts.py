"""A Fact with every field defaulted, so a test names only what it is about."""

from datetime import UTC, datetime

from pinecall.types import Fact

# The moment every fact in ring 0 is judged from; a learned date is relative to it.
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

THE_CONTACT = "contact-1"


def a_fact(
    id: str,
    text: str = "prefiere turnos por la mañana",
    *,
    category: str | None = "preference",
    learned: datetime = NOW,
    invalidated: datetime | None = None,
) -> Fact:
    """One fact of the one contact, held since `learned`, unscored."""
    return Fact(
        id=id,
        contact=THE_CONTACT,
        text=text,
        category=category,
        source=None,
        valid_from=learned,
        invalidated_at=invalidated,
        score=0.0,
    )
