"""Tests for which sections a search hands back: one per section, the best pages all theirs."""

from pinecall.retrieval._sections import PAGES_GIVING_ALL, sections_chosen


def row(path: str, heading: str) -> dict[str, object]:
    """A hit's row, as the search reads it."""
    return {"base": "docs", "path": path, "heading": heading}


def test_two_hits_in_one_section_come_back_once() -> None:
    rows = [row("a.md", "A / Clase"), row("a.md", "A / Clase"), row("a.md", "A / Uso")]
    assert sections_chosen(rows, k=8) == [0, 2]


def test_the_best_pages_give_every_section_and_a_later_page_only_its_best() -> None:
    rows = [
        row("a.md", "A / Uno"),
        row("b.md", "B / Uno"),
        row("c.md", "C / Uno"),
        row("a.md", "A / Dos"),
        row("c.md", "C / Dos"),
        row("b.md", "B / Dos"),
    ]
    assert PAGES_GIVING_ALL == 2
    assert sections_chosen(rows, k=8) == [0, 1, 2, 3, 5]


def test_k_caps_the_sections_handed_back() -> None:
    rows = [row("a.md", f"A / {n}") for n in range(5)]
    assert sections_chosen(rows, k=3) == [0, 1, 2]
