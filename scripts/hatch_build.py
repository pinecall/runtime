"""The wheel carries the console and the widget scripts/console built, beside the package."""

from pathlib import Path
from typing import Any, override

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

NOT_BUILT = "no build/public: run scripts/console before building the wheel"


class Console(BuildHookInterface):
    """Add build/public to a wheel; an editable install serves nothing built."""

    @override
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        """Put the built pages in the wheel, or refuse a wheel without pages."""
        if version == "editable":
            return
        built = Path(self.root) / "build" / "public"
        if not built.is_dir():
            raise RuntimeError(NOT_BUILT)
        build_data["force_include"][str(built)] = "pinecall/public"
