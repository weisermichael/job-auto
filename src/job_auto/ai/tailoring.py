"""Resume tailoring engine — rendercv YAML-based."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import yaml

from job_auto.ai.client import ai_client
from job_auto.config import config
from job_auto.models.job_posting import JobPosting
from job_auto.utils.logging import get_logger

logger = get_logger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "tailor_resume.md"

_SYSTEM_PROMPT = (
    "You are an expert resume writer. Follow the instructions exactly. "
    "CRITICAL: Never add technologies or experience the candidate does not have. "
    "CRITICAL: Resume must fit one page. Return ONLY valid JSON — no prose, no markdown fences."
)


def _render(template: str, **kwargs: str) -> str:
    """Replace {key} placeholders without touching other braces (e.g. JSON examples)."""
    for key, value in kwargs.items():
        template = template.replace(f"{{{key}}}", value)
    return template


def load_base_resume() -> str:
    """Load the base resume YAML text from data/resume.yaml."""
    path = config.resume_yaml_path
    if not path.exists():
        raise FileNotFoundError(
            f"Base resume not found at {path}. "
            "Place your resume.yaml in the data/ directory."
        )
    return path.read_text(encoding="utf-8")


def tailor_resume(job: JobPosting, base_resume: str | None = None) -> dict[str, Any]:
    """
    Call Claude to tailor the resume for the given job.

    Returns a dict with keys: cv_sections, cv_headline, keywords_matched, changes_summary.
    """
    if base_resume is None:
        base_resume = load_base_resume()

    template = _PROMPT_PATH.read_text(encoding="utf-8")
    user_prompt = _render(
        template,
        base_resume_yaml=base_resume[:8000],
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

    text = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    result = json.loads(text)
    logger.info(
        "tailoring_complete",
        job_id=job.id,
        keywords_matched=len(result.get("keywords_matched", [])),
    )
    return result


def merge_tailored_into_base(base_yaml_text: str, tailored: dict[str, Any]) -> dict[str, Any]:
    """
    Deep-copy the base YAML dict and replace only cv.sections (and optionally cv.headline).

    All other fields (cv.name, cv.email, design.theme, etc.) are preserved exactly.
    """
    merged = copy.deepcopy(yaml.safe_load(base_yaml_text))
    if tailored.get("cv_sections"):
        merged["cv"]["sections"] = tailored["cv_sections"]
    if tailored.get("cv_headline"):
        merged["cv"]["headline"] = tailored["cv_headline"]
    return merged


def tailored_to_yaml(merged_dict: dict[str, Any]) -> str:
    """Serialize the merged resume dict to a YAML string for storage and diffing."""
    return yaml.dump(merged_dict, allow_unicode=True, sort_keys=False)


def tailored_resume_text_for_cover_letter(merged_dict: dict[str, Any]) -> str:
    """Extract a plain-text summary of the resume for use as cover letter context."""
    cv = merged_dict.get("cv", {})
    sections = cv.get("sections", {})
    lines: list[str] = []

    if name := cv.get("name"):
        lines += [name, ""]

    for item in sections.get("summary", []):
        lines.append(str(item))
    if sections.get("summary"):
        lines.append("")

    for exp in sections.get("experience", []):
        lines.append(f"  {exp.get('position', '')} at {exp.get('company', '')}")
        for h in exp.get("highlights", [])[:3]:
            lines.append(f"    - {h}")
    if sections.get("experience"):
        lines.append("")

    for s in sections.get("skills", []):
        lines.append(f"  {s.get('label', '')}: {s.get('details', '')}")

    return "\n".join(lines)
