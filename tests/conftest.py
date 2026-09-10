"""Shared pytest setup for the ble_mqtt test suite.

The modules under test live at the repo root and import each other by bare name
(``from template import ...``), matching how they are deployed to
``/opt/ble-mqtt`` and run with that directory as the working directory. Putting
the repo root on ``sys.path`` here reproduces that layout for the tests.

Deliberately minimal: no shared fixtures. Each test module owns its own helpers,
so the suite can be extended one file at a time without contention here.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
