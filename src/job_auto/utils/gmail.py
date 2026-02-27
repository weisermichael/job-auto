"""Gmail API utilities for fetching LinkedIn security codes."""

from __future__ import annotations

import base64
import re
import time
from pathlib import Path

_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_SENDER = "security-noreply@linkedin.com"
_CODE_RE = re.compile(r"\b(\d{6})\b")


def authenticate(credentials_path: Path, token_path: Path) -> None:
    """Run OAuth2 consent flow and save token to token_path."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), _SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")


def fetch_linkedin_code(
    token_path: Path,
    timeout: int = 60,
    poll_interval: int = 3,
) -> str:
    """Poll Gmail for a LinkedIn 6-digit security code.

    Raises TimeoutError if the code is not found within `timeout` seconds.
    """
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)
    service = build("gmail", "v1", credentials=creds)
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        results = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=f"from:{_SENDER} newer_than:2m",
                maxResults=1,
            )
            .execute()
        )
        messages = results.get("messages", [])
        if messages:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=messages[0]["id"], format="full")
                .execute()
            )
            body = _extract_body(msg)
            match = _CODE_RE.search(body)
            if match:
                return match.group(1)
        time.sleep(poll_interval)

    raise TimeoutError("LinkedIn security code not found in Gmail within timeout")


def _extract_body(msg: dict) -> str:
    """Extract plain text body from a Gmail message payload."""
    payload = msg.get("payload", {})
    return _decode_parts(payload)


def _decode_parts(payload: dict) -> str:
    parts = payload.get("parts", [])
    if parts:
        text_parts = []
        for part in parts:
            mime = part.get("mimeType", "")
            if mime == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    text_parts.append(
                        base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                    )
            elif mime.startswith("multipart/"):
                text_parts.append(_decode_parts(part))
        return "\n".join(text_parts)
    else:
        # Leaf part
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        return ""
