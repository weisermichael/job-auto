"""Shared exception types for automation bots."""


class SessionExpiredError(RuntimeError):
    """The automation session expired mid-run; the bot was redirected away from the target page."""

    def __init__(self, url: str = "") -> None:
        self.url = url
        super().__init__(
            f"Session expired — browser redirected to: {url}" if url else "Session expired"
        )
