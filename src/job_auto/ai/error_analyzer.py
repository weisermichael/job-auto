"""Failure analysis → corrective action via Claude vision."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from job_auto.ai.client import ai_client
from job_auto.config import config
from job_auto.utils.logging import get_logger

logger = get_logger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "analyze_failure.md"

def _render(template: str, **kwargs: str) -> str:
    """Replace {key} placeholders without touching other braces (e.g. JSON examples)."""
    for key, value in kwargs.items():
        template = template.replace(f"{{{key}}}", value)
    return template


_SYSTEM_PROMPT = """You are an expert web automation debugger.
You will be shown a screenshot and DOM snippet from a failed Playwright automation run.
Return ONLY valid JSON matching the specified schema."""


def analyze_failure(
    *,
    error_message: str,
    failed_step: dict[str, Any],
    procedure: dict[str, Any],
    dom_snippet: str,
    screenshot_path: Optional[Path] = None,
) -> dict[str, Any]:
    """
    Analyze a Playwright automation failure and return a corrective action.

    Returns dict with keys: diagnosis, page_state, step_to_retry,
    corrective_action, explanation, update_kb, abort_reason.
    """
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    text_prompt = _render(
        template,
        error_message=error_message,
        failed_step=json.dumps(failed_step, indent=2),
        procedure=json.dumps(procedure, indent=2),
        dom_snippet=dom_snippet[:8000],
    )

    logger.info(
        "analyzing_failure",
        step=failed_step.get("order"),
        error=error_message[:120],
    )

    if screenshot_path and screenshot_path.exists():
        raw = ai_client.complete_with_image(
            model=config.fast_model,
            system=_SYSTEM_PROMPT,
            text_prompt=text_prompt,
            image_path=screenshot_path,
            max_tokens=1024,
        )
    else:
        raw = ai_client.complete(
            model=config.fast_model,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text_prompt}],
            max_tokens=1024,
            temperature=0.1,
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
    logger.info(
        "failure_analysis_complete",
        diagnosis=result.get("diagnosis", "")[:100],
        step_to_retry=result.get("step_to_retry"),
    )
    return result
