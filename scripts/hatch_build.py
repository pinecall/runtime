"""The wheel carries the console and the widget scripts/console built, and the box's own infra/."""

from pathlib import Path
from typing import Any, override

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

NOT_BUILT = "no build/public: run scripts/console before building the wheel"

# What `pinecall-runtime box up` makes a machine from: the box's files and the Postgres image;
# and the open stack's servers and row, for a box that runs on its own GPU.
INFRA = ("box", "postgres", "models")

# Pinecall's own backup key: a box someone else runs encrypts to the key they give `box up`.
NOT_SHIPPED = frozenset({"backup.age.pub"})


class Console(BuildHookInterface):
    """Add build/public and infra/ to a wheel; an editable install serves neither."""

    @override
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        """Put the built pages and the box's files in the wheel, or refuse a wheel without pages."""
        if version == "editable":
            return
        root = Path(self.root)
        built = root / "build" / "public"
        if not built.is_dir():
            raise RuntimeError(NOT_BUILT)
        build_data["force_include"][str(built)] = "pinecall/public"
        for part in INFRA:
            for path in sorted((root / "infra" / part).rglob("*")):
                if path.is_file() and path.name not in NOT_SHIPPED:
                    shipped = path.relative_to(root / "infra").as_posix()
                    build_data["force_include"][str(path)] = f"pinecall/infra/{shipped}"
