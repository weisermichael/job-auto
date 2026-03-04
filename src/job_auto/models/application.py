"""ApplicationRecord domain model."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional


class StrEnum(str, Enum):
    pass

from pydantic import BaseModel, field_validator


class ApplicationStatus(StrEnum):
    DISCOVERED = "discovered"
    QUEUED = "queued"
    TAILORING = "tailoring"
    REVIEW_PENDING = "review_pending"
    REVIEW_REJECTED = "review_rejected"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    FAILED = "failed"
    NEEDS_ANSWERS = "needs_answers"
    RETRY = "retry"
    RESPONDED = "responded"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class ApplicationRecord(BaseModel):
    id: str
    job_id: str
    status: ApplicationStatus = ApplicationStatus.DISCOVERED
    resume_path: Optional[str] = None
    cover_letter_path: Optional[str] = None
    tailored_resume_text: Optional[str] = None
    cover_letter_text: Optional[str] = None
    autonomous_mode: bool = False
    created_at: datetime
    submitted_at: Optional[datetime] = None
    failure_count: int = 0
    last_failure_reason: Optional[str] = None
    last_failure_screenshot: Optional[str] = None
    last_correction: Optional[str] = None

    @field_validator("id", mode="before")
    @classmethod
    def default_id(cls, v: str | None) -> str:
        if not v:
            return uuid.uuid4().hex[:12]
        return v

    @field_validator("created_at", mode="before")
    @classmethod
    def default_created_at(cls, v: datetime | None) -> datetime:
        return v or datetime.utcnow()

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            ApplicationStatus.SUBMITTED,
            ApplicationStatus.REVIEW_REJECTED,
            ApplicationStatus.WITHDRAWN,
            ApplicationStatus.OFFER,
        }

    @property
    def needs_retry(self) -> bool:
        return self.status == ApplicationStatus.FAILED and self.failure_count < 3
