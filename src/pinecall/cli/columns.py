"""How the CLI prints a table: every column as wide as its widest value, and nothing cut."""

from collections.abc import Sequence


def aligned_columns(rows: Sequence[tuple[str, ...]]) -> list[str]:
    """Every column as wide as its widest value: nothing is cut to make a table line up."""
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    return [
        "  ".join(value.ljust(width) for value, width in zip(row, widths, strict=True)).rstrip()
        for row in rows
    ]
