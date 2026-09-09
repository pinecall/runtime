"""The scripts are programs, not a package: this puts their directory where an import finds it."""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
