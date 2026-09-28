"""A fixture that breaks rule 18: things out of order."""


def first() -> int:
    """A public function before the constants."""
    return LATER


LATER = 1


def _helper() -> int:
    return 2


class Late:
    """A class after a private helper."""
