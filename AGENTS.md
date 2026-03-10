# AGENTS.md

Guidelines for agentic coding agents working in this repository.

## Commands

```bash
# Install (editable mode)
pip install -e .
playwright install chromium

# CLI usage
job-auto apply <url> [--dry-run] [--auto] [--tailor]
job-auto scan indeed -q "senior python engineer" --limit 20
job-auto scan linkedin -q "backend engineer" --auth-flow --easy-apply
job-auto apply-all --limit 5 [--tailor]
job-auto submit-queued --limit 10
job-auto retry-failed [--auto]
job-auto answer-questions [--board linkedin] [--retry]
job-auto retry-needs-answers
job-auto verify-answers [--board linkedin]
job-auto jobs [--unapplied] [--board linkedin] [--id <job-id>]
job-auto review
job-auto status [-s submitted]
job-auto browse          # Textual TUI
job-auto mode auto|review
job-auto kb show [-b linkedin]
job-auto gmail-auth      # one-time OAuth setup

# Lint and format
ruff check src/
ruff format src/

# Type check
mypy src/

# Tests
pytest
pytest tests/path/to/test_file.py::test_name -v
pytest tests/path/to/test_file.py -k "pattern" -v
```

## Code Style

### Imports

```python
from __future__ import annotations

import stdlib_module
from stdlib import something

import third_party
from third_party import Thing

from local.module import LocalClass
```

- Always put `from __future__ import annotations` first (enables modern type syntax)
- Group: stdlib → third-party → local, with blank lines between groups
- Sort alphabetically within each group (ruff handles this)

### Formatting

- Line length: 100 characters (configured in pyproject.toml)
- Use ruff for formatting: `ruff format src/`
- No trailing whitespace
- Blank lines: 2 before class/function definitions at module level, 1 inside classes

### Types

- Use modern Python 3.12 syntax: `X | None`, `list[str]`, `dict[str, int]`
- Avoid `Optional[X]` — use `X | None` instead
- Avoid `Union[A, B]` — use `A | B` instead
- Use `typing.TYPE_CHECKING` for import-time type-only imports
- Pydantic models: use `@field_validator` with `mode="before"` for defaults

### Naming

- Functions/variables: `snake_case`
- Classes/enums: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Private attributes: `_leading_underscore`
- Module-level logger: `logger = get_logger(__name__)`

### Docstrings

```python
def function(arg: str) -> bool:
    """One-line description in imperative mood."""
    ...

def complex_function(arg: str) -> bool:
    """One-line summary.

    Extended description if needed.

    Args:
        arg: Description of arg.

    Returns:
        Description of return value.
    """
    ...
```

### Comments and Section Separators

```python
# ──────────────────────────────────────────────────────────
# Section title
# ──────────────────────────────────────────────────────────
```

Use this pattern to visually group related code sections.

### Error Handling

- Raise custom exceptions from `automation/exceptions.py` for automation errors
- Include context in exception messages
- Use structured logging via `logger.error("event_name", key=value)` not `logger.error(f"...")`

```python
class CustomError(RuntimeError):
    def __init__(self, url: str = "") -> None:
        self.url = url
        super().__init__(f"Descriptive message: {url}" if url else "Descriptive message")
```

### Async Patterns

- All I/O operations are async
- Use async context managers for resources:

```python
async with scraper:
    job = await scraper.parse(url)

with get_session() as session:
    repo.upsert_job(session, job)
```

### Database Sessions

- ALWAYS use `with get_session() as session:` — never manage sessions manually
- Session auto-commits on success, auto-rolls-back on exception

## Architecture

### Pipeline Flow

```
scan → DB → (tailor) → review → submit (Playwright) → self-heal on failure
```

### Key Entry Points

- `src/job_auto/pipeline.py` — `Pipeline` class orchestrates all steps
- `src/job_auto/cli.py` — Click commands; async code crosses to sync via `asyncio.run()`

### Domain Models vs ORM

- Domain models (`models/*.py`): Pure Pydantic classes, no DB dependencies
- ORM models (`db/models.py`): SQLAlchemy declarative classes
- Repository (`db/repository.py`): Conversion functions `_orm_to_*` and `_*_to_orm`

### Config

Access via the `config` singleton from `job_auto.config`:

```python
from job_auto.config import config

path = config.storage_dir / "jobs.db"
api_key = config.anthropic_api_key.get_secret_value()
```

Config is a lazy proxy — importing modules never crashes without a `.env`.

### AI Prompt Templates

Templates in `ai/prompts/*.md` use `{placeholder}` syntax. Use the `_render()` helper:

```python
def _render(template: str, **kwargs: str) -> str:
    """Replace {key} placeholders without touching other braces."""
    for key, value in kwargs.items():
        template = template.replace(f"{{{key}}}", value)
    return template
```

NEVER use `str.format()` on prompt templates — JSON examples contain literal `{...}` braces.

### Automation Procedure Pattern

Automation steps are data, not code. Each board defines a procedure loaded from the knowledge base:

```python
@abstractmethod
def load_procedure(self) -> ApplicationProcedure:
    """Load the application procedure from the knowledge base."""

@abstractmethod
async def execute_step(self, step: ProcedureStep, context: dict[str, str]) -> bool:
    """Execute a single procedure step. Return True on success."""
```

### Application Status Lifecycle

```
DISCOVERED → QUEUED → TAILORING → REVIEW_PENDING → SUBMITTING → SUBMITTED
                                                         ↓
                                                      FAILED (retry up to 3x)
                                                         ↓
                                                   NEEDS_ANSWERS (human input)
```

Terminal states: `SUBMITTED`, `REVIEW_REJECTED`, `OFFER`, `WITHDRAWN`, `EXPIRED`

### Knowledge Base

- Location: `storage/knowledge_base.json`
- Access: `kb_store` singleton from `knowledge_base/store.py`
- Contains: procedures, Q&A cache, selector patches, AI notes

### Logging

Use structlog with event-based key-value pairs:

```python
logger.info("job_ingested", job_id=job.id, title=job.title)
logger.warning("retry_attempt", attempt=2, error=str(e))
logger.error("submission_failed", app_id=app.id, reason=reason)
```
