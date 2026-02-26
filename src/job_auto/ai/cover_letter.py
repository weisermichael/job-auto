"""Cover letter generator using Claude."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from job_auto.ai.client import ai_client
from job_auto.config import config
from job_auto.models.job_posting import JobPosting
from job_auto.utils.logging import get_logger

logger = get_logger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "cover_letter.md"

def _render(template: str, **kwargs: str) -> str:
    """Replace {key} placeholders without touching other braces (e.g. JSON examples)."""
    for key, value in kwargs.items():
        template = template.replace(f"{{{key}}}", value)
    return template


_SYSTEM_PROMPT = """You are a professional cover letter writer.
Return ONLY valid JSON — no prose, no markdown fences.
Every claim must be grounded in the tailored resume provided."""


def generate_cover_letter(
    job: JobPosting,
    tailored_resume_md: str,
    hiring_manager: str = "Hiring Manager",
    candidate_name: str = "",
) -> dict[str, Any]:
    """
    Generate a cover letter for the given job.

    Returns a dict with keys: salutation, paragraph_1, paragraph_2, paragraph_3,
    closing, full_text.
    """
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    user_prompt = _render(
        template,
        tailored_resume=tailored_resume_md[:4000],
        job_description=job.description[:4000],
        job_title=job.title,
        company=job.company,
        hiring_manager=hiring_manager,
        candidate_name=candidate_name,
    )

    logger.info("generating_cover_letter", job_id=job.id, company=job.company)

    raw = ai_client.complete(
        model=config.tailor_model,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=1500,
        temperature=0.3,
    )

    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    if text.endswith("```"):
        text = text[:-3].strip()

    result = json.loads(text)

    # Enforce the candidate name in the closing regardless of what Claude produced
    if candidate_name:
        closing = result.get("closing", "Sincerely,")
        # Strip any name Claude may have appended, then add the real one
        closing_lines = closing.strip().splitlines()
        sign_off = closing_lines[0] if closing_lines else "Sincerely,"
        result["closing"] = f"{sign_off}\n\n{candidate_name}"
        # Rebuild full_text with the corrected closing
        if result.get("full_text"):
            result["full_text"] = cover_letter_to_markdown(result)

    logger.info("cover_letter_complete", job_id=job.id)
    return result


def cover_letter_to_markdown(letter: dict[str, Any]) -> str:
    """Convert cover letter dict to plain Markdown for PDF rendering."""
    if "full_text" in letter and letter["full_text"]:
        return letter["full_text"]

    parts = [
        letter.get("salutation", ""),
        "",
        letter.get("paragraph_1", ""),
        "",
        letter.get("paragraph_2", ""),
        "",
        letter.get("paragraph_3", ""),
        "",
        letter.get("closing", ""),
    ]
    return "\n".join(parts)
