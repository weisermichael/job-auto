"""KnowledgeBaseStore: load/save/query JSON knowledge base."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from job_auto.config import config
from job_auto.models.procedure import ApplicationProcedure
from job_auto.utils.logging import get_logger

logger = get_logger(__name__)

_INITIAL_KB: dict[str, Any] = {
    "linkedin": {
        "version": 1,
        "steps": [],
        "selectors": {},
        "failure_patches": {
            "timeout::apply-button": {
                "selector": ".jobs-apply-button--top-card",
                "wait_after_ms": 1500,
            },
            "element-not-found::phone": {
                "xpath": "//input[@name='phoneNumber']",
            },
        },
        "ai_notes": [
            "LinkedIn rate-limits after ~10 Easy Applies/day",
            "CAPTCHA may appear on fresh sessions — wait and solve manually",
        ],
        "success_count": 0,
        "failure_count": 0,
        "last_updated": datetime.utcnow().isoformat(),
    },
    "indeed": {
        "version": 1,
        "steps": [],
        "selectors": {},
        "failure_patches": {},
        "ai_notes": [],
        "success_count": 0,
        "failure_count": 0,
        "last_updated": datetime.utcnow().isoformat(),
    },
    "nodesk": {
        "version": 1,
        "steps": [],
        "selectors": {},
        "failure_patches": {},
        "ai_notes": [],
        "success_count": 0,
        "failure_count": 0,
        "last_updated": datetime.utcnow().isoformat(),
    },
}


def _norm_url(url: str) -> str:
    return url.rstrip("/").strip()


class KnowledgeBaseStore:
    """Thread-safe JSON knowledge base for application procedures."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        self._loaded = False

    def _path(self) -> Path:
        return config.knowledge_base_path

    def _load(self) -> None:
        if self._loaded:
            return
        path = self._path()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        else:
            self._data = _INITIAL_KB.copy()
            self._save_unlocked()
        self._loaded = True

    def _save_unlocked(self) -> None:
        """Save without acquiring lock (caller must hold it)."""
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, default=str)

    def get_raw(self, board: str) -> Optional[dict[str, Any]]:
        with self._lock:
            self._load()
            return self._data.get(board)

    def get_procedure(self, board: str) -> Optional[ApplicationProcedure]:
        """Return an ApplicationProcedure for the board, or None if not in KB."""
        raw = self.get_raw(board)
        if not raw or not raw.get("steps"):
            return None
        try:
            return ApplicationProcedure(
                board=board,
                version=raw.get("version", 1),
                steps=raw.get("steps", []),
                selectors=raw.get("selectors", {}),
                failure_patches=raw.get("failure_patches", {}),
                ai_notes=raw.get("ai_notes", []),
                success_count=raw.get("success_count", 0),
                failure_count=raw.get("failure_count", 0),
                last_updated=datetime.fromisoformat(
                    raw.get("last_updated", datetime.utcnow().isoformat())
                ),
            )
        except Exception as e:
            logger.warning("kb_parse_error", board=board, error=str(e))
            return None

    def update(self, board: str, updates: dict[str, Any]) -> None:
        """Merge updates into the board's KB entry."""
        with self._lock:
            self._load()
            if board not in self._data:
                self._data[board] = {}
            self._data[board].update(updates)
            self._data[board]["last_updated"] = datetime.utcnow().isoformat()
            self._save_unlocked()

    def get_ai_notes(self, board: str) -> list[str]:
        raw = self.get_raw(board)
        return raw.get("ai_notes", []) if raw else []

    def get_qa_cache(self, board: str) -> dict[str, str]:
        raw = self.get_raw(board)
        if not raw:
            return {}
        return {
            re.sub(r"\s+", " ", k.lower()).strip(): v
            for k, v in raw.get("qa_cache", {}).items()
        }

    def set_qa_answer(self, board: str, key: str, answer: str) -> None:
        key = re.sub(r"\s+", " ", key.lower()).strip()
        with self._lock:
            self._load()
            self._data.setdefault(board, {}).setdefault("qa_cache", {})[key] = answer
            self._data[board]["last_updated"] = datetime.utcnow().isoformat()
            self._save_unlocked()

    def record_pending_questions(self, board: str, job_url: str, questions: list[dict]) -> None:
        """Persist unanswered required questions keyed by job URL for later review."""
        job_url = _norm_url(job_url)
        with self._lock:
            self._load()
            self._data.setdefault(board, {}).setdefault("pending_questions", {})[job_url] = questions
            self._data[board]["last_updated"] = datetime.utcnow().isoformat()
            self._save_unlocked()

    def get_pending_questions(self, board: str, job_url: str) -> list[dict]:
        job_url = _norm_url(job_url)
        raw = self.get_raw(board)
        return list(raw.get("pending_questions", {}).get(job_url, [])) if raw else []

    def resolve_pending_question(self, board: str, job_url: str, question_label: str) -> None:
        """Remove one answered question from pending. Removes the job URL entry when the last question is resolved."""
        with self._lock:
            self._load()
            if board not in self._data:
                return
            job_url = _norm_url(job_url)
            pending = self._data[board].setdefault("pending_questions", {})
            questions = [q for q in pending.get(job_url, []) if q.get("label") != question_label]
            if questions:
                pending[job_url] = questions
            else:
                pending.pop(job_url, None)
            self._data[board]["last_updated"] = datetime.utcnow().isoformat()
            self._save_unlocked()

    def list_all_pending_questions(self, board: str) -> dict[str, list[dict]]:
        """Return all {job_url: questions} pending for a board."""
        raw = self.get_raw(board)
        return dict(raw.get("pending_questions", {})) if raw else {}

    def get_qa_pending_verification(self, board: str) -> dict[str, dict]:
        """Return {cache_key: {label, answer, type}} for all pattern-matched answers awaiting review."""
        raw = self.get_raw(board)
        return dict(raw.get("qa_pending_verification", {})) if raw else {}

    def add_qa_pending_verification(self, board: str, key: str, label: str, answer: str, ftype: str) -> None:
        """Store a pattern-matched answer under qa_pending_verification for async user review."""
        key = re.sub(r"\s+", " ", key.lower()).strip()
        with self._lock:
            self._load()
            self._data.setdefault(board, {}).setdefault("qa_pending_verification", {})[key] = {
                "label": label,
                "answer": answer,
                "type": ftype,
            }
            self._data[board]["last_updated"] = datetime.utcnow().isoformat()
            self._save_unlocked()

    def resolve_qa_pending_verification(self, board: str, key: str, confirmed_answer: str) -> None:
        """Confirm a pending answer: remove from qa_pending_verification and write to qa_cache."""
        key = re.sub(r"\s+", " ", key.lower()).strip()
        with self._lock:
            self._load()
            board_data = self._data.setdefault(board, {})
            board_data.setdefault("qa_pending_verification", {}).pop(key, None)
            board_data.setdefault("qa_cache", {})[key] = confirmed_answer
            board_data["last_updated"] = datetime.utcnow().isoformat()
            self._save_unlocked()

    def get_failure_patches(self, board: str) -> dict[str, Any]:
        raw = self.get_raw(board)
        return raw.get("failure_patches", {}) if raw else {}

    def summary(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            self._load()
        result = {}
        for board, data in self._data.items():
            result[board] = {
                "version": data.get("version", 1),
                "success_count": data.get("success_count", 0),
                "failure_count": data.get("failure_count", 0),
                "patches": len(data.get("failure_patches", {})),
                "notes": len(data.get("ai_notes", [])),
                "last_updated": data.get("last_updated", ""),
            }
        return result


# Module-level singleton
kb_store = KnowledgeBaseStore()
