from __future__ import annotations

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "imported": frozenset({"extracted", "failed"}),
    "extracted": frozenset({"cleaned", "failed"}),
    "cleaned": frozenset({"chunked", "failed"}),
    "chunked": frozenset({"ready", "failed"}),
    "ready": frozenset({"synthesizing", "playable", "failed"}),
    "synthesizing": frozenset({"playable", "paused", "failed"}),
    "playable": frozenset({"synthesizing", "paused", "complete", "failed"}),
    "paused": frozenset({"synthesizing", "playable", "failed"}),
    "failed": frozenset({"ready", "synthesizing"}),
    "complete": frozenset({"playable", "synthesizing"}),
}


def require_transition(current: str, target: str) -> None:
    """Raise when a persistent state transition is illegal."""
    if target == current:
        return
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise ValueError(f"illegal state transition: {current} -> {target}")
