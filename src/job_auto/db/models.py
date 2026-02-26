"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class JobPostingORM(Base):
    __tablename__ = "job_postings"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    company: Mapped[str] = mapped_column(String(256))
    board: Mapped[str] = mapped_column(String(32))
    url: Mapped[str] = mapped_column(String(2048), unique=True)
    description: Mapped[str] = mapped_column(Text)
    requirements: Mapped[str] = mapped_column(Text, default="[]")  # JSON list
    nice_to_haves: Mapped[str] = mapped_column(Text, default="[]")
    responsibilities: Mapped[str] = mapped_column(Text, default="[]")
    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    remote: Mapped[bool] = mapped_column(Boolean, default=False)
    experience_level: Mapped[str] = mapped_column(String(32), default="unknown")
    easy_apply_available: Mapped[bool] = mapped_column(Boolean, default=False)
    date_found: Mapped[datetime] = mapped_column(DateTime)
    tech_stack: Mapped[str] = mapped_column(Text, default="[]")
    keywords: Mapped[str] = mapped_column(Text, default="[]")

    applications: Mapped[list[ApplicationRecordORM]] = relationship(
        "ApplicationRecordORM", back_populates="job", cascade="all, delete-orphan"
    )


class ApplicationRecordORM(Base):
    __tablename__ = "application_records"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("job_postings.id"))
    status: Mapped[str] = mapped_column(String(32), default="discovered")
    resume_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    cover_letter_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tailored_resume_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_letter_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    autonomous_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_failure_screenshot: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_correction: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped[JobPostingORM] = relationship("JobPostingORM", back_populates="applications")
