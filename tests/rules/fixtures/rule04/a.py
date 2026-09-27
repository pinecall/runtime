"""Fixture: six lines written twice."""


def fold(rows: list[int]) -> int:
    """Sum the even rows, twice as much for the ones over ten."""
    total = 0
    for row in rows:
        if row % 2:
            continue
        if row > 10:
            total += row * 2
        else:
            total += row
    return total
