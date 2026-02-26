"""Anthropic SDK wrapper with retry logic and token tracking."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Optional

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from job_auto.config import config
from job_auto.utils.logging import get_logger

logger = get_logger(__name__)

_TOTAL_INPUT_TOKENS = 0
_TOTAL_OUTPUT_TOKENS = 0


def get_token_usage() -> dict[str, int]:
    return {"input": _TOTAL_INPUT_TOKENS, "output": _TOTAL_OUTPUT_TOKENS}


class AIClient:
    """Thin wrapper around the Anthropic SDK."""

    def __init__(self) -> None:
        self._client = anthropic.Anthropic(
            api_key=config.anthropic_api_key.get_secret_value()
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> str:
        """Send a completion request; return the text content."""
        global _TOTAL_INPUT_TOKENS, _TOTAL_OUTPUT_TOKENS

        response = self._client.messages.create(
            model=model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        _TOTAL_INPUT_TOKENS += response.usage.input_tokens
        _TOTAL_OUTPUT_TOKENS += response.usage.output_tokens

        logger.debug(
            "ai_complete",
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        return response.content[0].text

    def complete_with_image(
        self,
        *,
        model: str,
        system: str,
        text_prompt: str,
        image_path: Path,
        max_tokens: int = 2048,
    ) -> str:
        """Send a message that includes a screenshot image."""
        with open(image_path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")

        suffix = image_path.suffix.lower()
        media_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(suffix, "image/png")

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        },
                    },
                    {"type": "text", "text": text_prompt},
                ],
            }
        ]
        return self.complete(
            model=model, system=system, messages=messages, max_tokens=max_tokens
        )


# Module-level singleton
ai_client = AIClient()
