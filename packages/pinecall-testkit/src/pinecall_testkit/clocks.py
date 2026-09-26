"""A clock a test moves by hand: what it answers, and how far every reading moves it."""


class Clock:
    """Time that moves only when the test says so, or a step per reading when it asks for one."""

    def __init__(self, now: float = 1_000.0, *, tick: float = 0.0) -> None:
        self.now = now
        self.tick = tick

    def __call__(self) -> float:
        self.now += self.tick
        return self.now
