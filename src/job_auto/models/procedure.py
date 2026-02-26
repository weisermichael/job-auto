"""ApplicationProcedure models for knowledge base."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class FormSelector(BaseModel):
    css: Optional[str] = None
    xpath: Optional[str] = None
    text: Optional[str] = None
    label: Optional[str] = None

    @property
    def primary(self) -> str:
        """Return the best available selector."""
        return self.css or self.xpath or self.text or self.label or ""


class ProcedureStep(BaseModel):
    order: int
    description: str
    action: str  # navigate | click | fill | upload | wait | submit | screenshot
    selector: Optional[str] = None
    value: Optional[str] = None  # static or {{template_var}}
    wait_after_ms: int = 500
    optional: bool = False

    def render_value(self, context: dict[str, str]) -> Optional[str]:
        """Interpolate {{template_var}} placeholders."""
        if not self.value:
            return None
        result = self.value
        for key, val in context.items():
            result = result.replace(f"{{{{{key}}}}}", val)
        return result


class ApplicationProcedure(BaseModel):
    board: str
    version: int = 1
    steps: list[ProcedureStep] = []
    selectors: dict[str, FormSelector] = {}
    success_count: int = 0
    failure_count: int = 0
    failure_patches: dict[str, Any] = {}  # error_signature → corrective step override
    ai_notes: list[str] = []
    last_updated: datetime

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.0
        return self.success_count / total
