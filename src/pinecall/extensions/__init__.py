"""Extensions: the named points a package installed beside the runtime plugs its policy into."""

from pinecall.extensions.loading import NoSuchExtension, extensions_from
from pinecall.extensions.points import Admitting, Extensions, unlimited

__all__ = ["Admitting", "Extensions", "NoSuchExtension", "extensions_from", "unlimited"]
