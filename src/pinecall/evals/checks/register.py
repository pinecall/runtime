"""The register scan: the words a business will not have its agent say, over what the agent said."""

from __future__ import annotations

from collections.abc import Sequence

from pinecall.evals.checks.replayed import Replayed
from pinecall.evals.checks.verdict import Verdict, failed, passed, skipped

CHECK = "register"

PUNCTUATION = ".,;:¿?¡!()\"'"

NOTHING_DECLARED = (
    "no words were declared for this call: send them as `banned` with the request, or point "
    "`pinecall eval` at a policy file"
)


def register(call: Replayed, banned: Sequence[str]) -> Verdict:
    """Whether the agent kept the register: no banned word in any of its turns, case aside."""
    if not banned:
        return skipped(CHECK, NOTHING_DECLARED)
    said = [(turn, word) for turn, text in enumerate(call.said, 1) for word in _found(text, banned)]
    if said:
        spoken = "; ".join(f"{word!r} in turn {turn}" for turn, word in said)
        return failed(CHECK, f"the agent said {spoken}")
    kept = f"none of the {len(banned)} declared word(s) was said in {len(call.said)} agent turns"
    return passed(CHECK, kept)


# Whole words, because a business that bans `tú` is not banning `tútem` and a substring scan over a
# transcript fails on its own examples — but a declared phrase is matched as a phrase, since that is
# the only way it could ever match.
def _found(text: str, banned: Sequence[str]) -> list[str]:
    """The declared words and phrases this turn used, in the order they were declared."""
    said = text.casefold()
    words = {word.strip(PUNCTUATION).casefold() for word in text.split()}
    return [
        declared
        for declared in banned
        if (declared.casefold() in said if " " in declared else declared.casefold() in words)
    ]
