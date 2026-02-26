"""Resume tailoring engine using Claude structured output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from job_auto.ai.client import ai_client
from job_auto.config import config
from job_auto.models.job_posting import JobPosting
from job_auto.utils.logging import get_logger

logger = get_logger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "tailor_resume.md"

_SYSTEM_PROMPT = """You are an expert resume writer. You must follow the instructions exactly.
CRITICAL: Never add technologies, tools, companies, or achievements that are not in the base resume.
CRITICAL: The resume MUST fit on one page. Cut or condense ruthlessly — prioritize relevance to the role.
Return ONLY valid JSON — no prose, no markdown fences."""


def _render(template: str, **kwargs: str) -> str:
    """Replace {key} placeholders without touching other braces (e.g. JSON examples)."""
    for key, value in kwargs.items():
        template = template.replace(f"{{{key}}}", value)
    return template


def load_base_resume() -> str:
    """Load the base resume from data/resume.md."""
    path = config.resume_md_path
    if not path.exists():
        raise FileNotFoundError(
            f"Base resume not found at {path}. "
            "Please place your resume.md in the data/ directory."
        )
    return path.read_text(encoding="utf-8")


def tailor_resume(job: JobPosting, base_resume: str | None = None) -> dict[str, Any]:
    """
    Tailor the base resume for the given job posting.

    Returns a dict with keys: summary, experience, education, skills,
    keywords_matched, changes_summary.
    """
    if base_resume is None:
        base_resume = load_base_resume()

    template = _PROMPT_PATH.read_text(encoding="utf-8")
    user_prompt = _render(
        template,
        base_resume=base_resume,
        job_description=job.description[:6000],
        job_title=job.title,
        company=job.company,
    )

    logger.info("tailoring_resume", job_id=job.id, company=job.company, title=job.title)

    raw = ai_client.complete(
        model=config.tailor_model,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=4096,
        temperature=0.1,
    )

    # Strip any accidental markdown fences
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    if text.endswith("```"):
        text = text[:-3].strip()

    result = json.loads(text)
    logger.info(
        "tailoring_complete",
        job_id=job.id,
        keywords_matched=len(result.get("keywords_matched", [])),
    )
    return result


def tailored_to_markdown(tailored: dict[str, Any], candidate_name: str = "") -> str:
    """Convert the tailored resume dict back to Markdown for PDF rendering."""
    lines: list[str] = []

    if candidate_name:
        lines.append(f"# {candidate_name}")
        lines.append("")

    if tailored.get("summary"):
        lines.append("## Summary")
        lines.append("")
        lines.append(tailored["summary"])
        lines.append("")

    if tailored.get("experience"):
        lines.append("## Experience")
        lines.append("")
        for exp in tailored["experience"]:
            lines.append(f"### {exp.get('title', '')} — {exp.get('company', '')}")
            if exp.get("dates"):
                lines.append(f"*{exp['dates']}*")
            lines.append("")
            for bullet in exp.get("bullets", []):
                lines.append(f"- {bullet}")
            lines.append("")

    if tailored.get("education"):
        lines.append("## Education")
        lines.append("")
        for edu in tailored["education"]:
            lines.append(f"### {edu.get('degree', '')} — {edu.get('institution', '')}")
            if edu.get("dates"):
                lines.append(f"*{edu['dates']}*")
            lines.append("")

    if tailored.get("skills"):
        lines.append("## Skills")
        lines.append("")
        lines.append(", ".join(tailored["skills"]))
        lines.append("")

    return "\n".join(lines)
