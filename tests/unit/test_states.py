from __future__ import annotations

import pytest

from local_tts_reader.domain.states import require_transition


def test_state_transition_contract() -> None:
    require_transition("ready", "synthesizing")
    require_transition("playable", "complete")

    with pytest.raises(ValueError, match="illegal state transition"):
        require_transition("imported", "complete")
