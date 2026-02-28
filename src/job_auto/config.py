"""Application configuration via pydantic-settings."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Anthropic
    anthropic_api_key: SecretStr

    # Job board credentials (optional)
    linkedin_email: str = ""
    linkedin_password: SecretStr = SecretStr("")

    # Candidate info
    candidate_name: str = ""

    # Behaviour toggles
    autonomous_mode: bool = False
    max_retries: int = 3
    daily_apply_limit: int = 10
    headless_browser: bool = True

    # Paths
    storage_dir: Path = Path("storage")
    data_dir: Path = Path("data")

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_file: Path = Path("storage/job_auto.log")

    # AI models
    tailor_model: str = "claude-opus-4-6"
    fast_model: str = "claude-sonnet-4-6"

    @field_validator("storage_dir", "data_dir", mode="before")
    @classmethod
    def make_path(cls, v: str | Path) -> Path:
        return Path(v)

    @property
    def db_path(self) -> Path:
        return self.storage_dir / "jobs.db"

    @property
    def knowledge_base_path(self) -> Path:
        return self.storage_dir / "knowledge_base.json"

    @property
    def resumes_dir(self) -> Path:
        return self.storage_dir / "resumes"

    @property
    def cover_letters_dir(self) -> Path:
        return self.storage_dir / "cover_letters"

    @property
    def screenshots_dir(self) -> Path:
        return self.storage_dir / "screenshots"

    @property
    def resume_md_path(self) -> Path:
        return self.data_dir / "resume.md"

    @property
    def resume_yaml_path(self) -> Path:
        return self.data_dir / "resume.yaml"

    @property
    def resume_docx_path(self) -> Path:
        return self.data_dir / "resume.docx"

    @property
    def criteria_path(self) -> Path:
        return self.data_dir / "criteria.yaml"

    @property
    def linkedin_session_path(self) -> Path:
        return self.storage_dir / "linkedin_session.json"

    @property
    def gmail_credentials_path(self) -> Path:
        return self.storage_dir / "gmail_credentials.json"

    @property
    def gmail_token_path(self) -> Path:
        return self.storage_dir / "gmail_token.json"

    def ensure_dirs(self) -> None:
        """Create all storage directories if they don't exist."""
        for d in [
            self.storage_dir,
            self.resumes_dir,
            self.cover_letters_dir,
            self.screenshots_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)


class _LazyConfig:
    """Deferred Config loader so the CLI can start without a .env file."""

    _instance: "Config | None" = None

    def _load(self) -> "Config":
        if self._instance is None:
            self._instance = Config()
        return self._instance

    def __getattr__(self, name: str):
        return getattr(self._load(), name)


# Module-level singleton — import this everywhere
config: Config = _LazyConfig()  # type: ignore[assignment]
