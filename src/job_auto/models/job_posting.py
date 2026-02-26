"""JobPosting domain model."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional


class StrEnum(str, Enum):
    pass

from pydantic import BaseModel, HttpUrl, field_validator


class JobBoard(StrEnum):
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    NODESK = "nodesk"
    GENERIC = "generic"


class ExperienceLevel(StrEnum):
    INTERN = "intern"
    ENTRY = "entry"
    MID = "mid"
    SENIOR = "senior"
    STAFF = "staff"
    PRINCIPAL = "principal"
    MANAGER = "manager"
    DIRECTOR = "director"
    UNKNOWN = "unknown"


class JobPosting(BaseModel):
    id: str
    title: str
    company: str
    board: JobBoard
    url: HttpUrl
    description: str
    requirements: list[str] = []
    nice_to_haves: list[str] = []
    responsibilities: list[str] = []
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    location: Optional[str] = None
    remote: bool = False
    experience_level: ExperienceLevel = ExperienceLevel.UNKNOWN
    easy_apply_available: bool = False
    date_found: datetime
    tech_stack: list[str] = []
    keywords: list[str] = []

    @field_validator("id", mode="before")
    @classmethod
    def default_id(cls, v: str | None) -> str:
        if not v:
            return uuid.uuid4().hex[:12]
        return v

    @field_validator("date_found", mode="before")
    @classmethod
    def default_date(cls, v: datetime | None) -> datetime:
        return v or datetime.utcnow()

    @property
    def url_str(self) -> str:
        return str(self.url)

    @property
    def salary_range(self) -> Optional[str]:
        if self.salary_min and self.salary_max:
            return f"${self.salary_min:,}–${self.salary_max:,}"
        if self.salary_min:
            return f"${self.salary_min:,}+"
        return None
