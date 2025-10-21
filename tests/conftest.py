import os
import sys
from pathlib import Path

# Ensure src/ is on sys.path for tests without requiring install
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
