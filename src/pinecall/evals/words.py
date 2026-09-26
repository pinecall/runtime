"""The words of a Spanish sentence as a scan reads them: punctuation off, case folded."""

from __future__ import annotations

PUNCTUATION = ".,;:¿?¡!()\"'"


def words_of(text: str) -> set[str]:
    """Every word in the text, stripped of the punctuation around it and folded."""
    return {word.strip(PUNCTUATION).casefold() for word in text.split()}
