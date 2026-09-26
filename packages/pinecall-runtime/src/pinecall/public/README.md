# public/

What the gateway serves as pages, built elsewhere and copied here by `scripts/console`:

| here | from | served at |
|---|---|---|
| `console/` | the console repo's vite build (`console/apps/console/dist`) | `/` and every path no door declared (`api/pages.py`) |
| `widget/pinecall-widget.js` | the widget repo | `/widget/pinecall-widget.js` |

Nothing here but this page is source: the two directories are git-ignored, because a build is not
a source, and shipped in the wheel as hatch `artifacts` (the runtime's `pyproject.toml`), because
the gateway cannot serve what it does not carry. Never edit them here; edit the repo they come
from and run `scripts/console` (which `make deploy` runs first). A checkout that never ran it
answers every page with a sentence saying so, not a blank one.
