"""ToolSpec: one tool's contract, the model's half and the platform's half. `when` is not here."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, get_args

from pinecall.types.refusal import DeclarationRefused

# read looks at the world. write changes it and can be undone. irreversible changes it for good —
# a booking sent, a payment, a cancellation — so the platform asks the caller first, never the
# model.
type SideEffect = Literal["read", "write", "irreversible"]

SIDE_EFFECTS: frozenset[str] = frozenset(get_args(SideEffect.__value__))

# What a model can call: one word of letters, digits and underscores. find_patient and findPatient
# are both fine; a space, a dot or a leading digit is not.
_A_NAME_A_MODEL_CAN_CALL = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ToolSpec:
    """One tool as the app declared it, judged here before the app's method ever runs."""

    name: str
    description: str
    parameters: Mapping[str, Any]
    side_effect: SideEffect = "read"
    pii: frozenset[str] = frozenset()
    confirm: str | None = None
    preview: int | None = None
    timeout_s: float = 30.0

    def __post_init__(self) -> None:
        if not _A_NAME_A_MODEL_CAN_CALL.match(self.name):
            raise DeclarationRefused(f"a tool name is one word a model can call, not {self.name!r}")
        if not self.description.strip():
            raise DeclarationRefused(
                f"tool {self.name}: without a description no model can choose it"
            )
        if self.parameters.get("type") != "object":
            raise DeclarationRefused(
                f"tool {self.name}: parameters are a JSON Schema of type object"
            )
        if self.side_effect not in SIDE_EFFECTS:
            raise DeclarationRefused(
                f"tool {self.name}: side_effect is one of {sorted(SIDE_EFFECTS)}, "
                f"not {self.side_effect!r}"
            )
        if self.side_effect == "irreversible" and not self.confirm:
            raise DeclarationRefused(
                f"tool {self.name}: an irreversible tool needs a confirm template to read back"
            )
        if unknown := self.pii - self.parameter_names:
            raise DeclarationRefused(
                f"tool {self.name}: pii names parameters the tool has; unknown: {sorted(unknown)}"
            )
        if self.preview is not None and self.preview < 1:
            raise DeclarationRefused(f"tool {self.name}: a preview shows at least one item")
        if self.timeout_s <= 0:
            raise DeclarationRefused(f"tool {self.name}: timeout_s is a positive number of seconds")

    @property
    def parameter_names(self) -> frozenset[str]:
        """The argument names the model may fill, read off the schema."""
        return frozenset(self.parameters.get("properties", {}))
