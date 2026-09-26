"""The files every knowledge test pushes, and an org of a test's own to push them under."""

from typing import Any
from uuid import uuid4

from pinecall.types import KnowledgeFile

CLINICA = KnowledgeFile(
    "clinica.md",
    "# Clínica Norte\n\n## Horarios\n\nAbrimos de lunes a viernes de nueve a dieciocho.\n\n"
    "## Turnos\n\nLos turnos se piden por teléfono o por la web, con el documento a mano.\n",
)
TARIFAS = KnowledgeFile(
    "tarifas.md",
    "# Tarifas\n\n## Revisión\n\nLa revisión cuesta cuarenta euros y dura media hora.\n\n"
    "## Limpieza\n\nLa limpieza dental cuesta sesenta euros.\n",
)

# A second base with nothing to do with the first: no word of it is a word of a question about
# the clinic, which is what makes it the right neighbour for a multi-base search.
VENDING = KnowledgeFile(
    "vending.md",
    "# Máquinas\n\n## Café\n\nLa máquina del pasillo acepta monedas de un euro.\n\n"
    "## Reposición\n\nEl proveedor repone los snacks cada martes por la mañana.\n",
)


async def an_org(connection: Any) -> str:
    """One more org, created now, named so no other test's rows can be mistaken for its own."""
    org = f"org-{uuid4().hex[:12]}"
    await connection.execute("insert into orgs (id, slug, name) values ($1, $1, $1)", org)
    return org
