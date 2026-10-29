"""Pytest configuration for path setup."""

import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock


PROJECT_ROOT = Path(__file__).parents[1].resolve()
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


# Stub robot SDK packages unavailable on Mac (pycairo/pygobject require native libs).
# Must be done before any app module is imported.
def _stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


for _pkg in ("reachy_mini", "reachy_mini_dances_library", "reachy_mini_toolbox"):
    if _pkg not in sys.modules:
        parent = _stub(_pkg)
        # Expose common attrs as MagicMocks so attribute access doesn't raise
        parent.ReachyMini = MagicMock
        parent.ReachyMiniApp = MagicMock


# Make tests reproducible by ignoring machine-specific profile/tool env config.
# Without this, importing config during test collection can pick up a developer's
# local .env and fail before tests run.
os.environ["REACHY_MINI_SKIP_DOTENV"] = "1"
os.environ.pop("REACHY_MINI_CUSTOM_PROFILE", None)
os.environ.pop("REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY", None)
os.environ.pop("REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY", None)
