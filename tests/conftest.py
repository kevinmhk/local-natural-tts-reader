from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fixture_root() -> Path:
    """Return the committed fixture root."""
    return Path(__file__).parent / "fixtures"
