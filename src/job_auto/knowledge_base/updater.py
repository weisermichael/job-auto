"""Write back successful/failed patterns to the knowledge base."""

from __future__ import annotations

from typing import Any

from job_auto.knowledge_base.store import kb_store
from job_auto.utils.logging import get_logger

logger = get_logger(__name__)


def record_success(board: str, steps_taken: list[dict[str, Any]]) -> None:
    """Increment success count and optionally update step timings."""
    raw = kb_store.get_raw(board) or {}
    count = raw.get("success_count", 0) + 1
    kb_store.update(board, {"success_count": count})
    logger.info("kb_success_recorded", board=board, total_success=count)


def record_patch(board: str, error_signature: str, correction: dict[str, Any]) -> None:
    """
    Save a successful AI correction as a reusable patch.

    error_signature — e.g. "timeout::apply-button" or "element-not-found::phone"
    """
    raw = kb_store.get_raw(board) or {}
    patches = raw.get("failure_patches", {})
    patches[error_signature] = correction

    kb_store.update(board, {"failure_patches": patches})
    logger.info("kb_patch_saved", board=board, sig=error_signature)


def record_failure(board: str, error: str) -> None:
    """Increment failure count and append an AI note about the error."""
    raw = kb_store.get_raw(board) or {}
    count = raw.get("failure_count", 0) + 1
    notes = raw.get("ai_notes", [])

    # Avoid duplicate notes
    note = f"Failure: {error[:120]}"
    if note not in notes:
        notes.append(note)

    kb_store.update(board, {"failure_count": count, "ai_notes": notes})
    logger.info("kb_failure_recorded", board=board, total_failures=count)


def add_ai_note(board: str, note: str) -> None:
    """Append a free-form AI observation to the board's notes."""
    raw = kb_store.get_raw(board) or {}
    notes = raw.get("ai_notes", [])
    if note not in notes:
        notes.append(note)
        kb_store.update(board, {"ai_notes": notes})
