# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (editable mode)
pip install -e .
playwright install chromium

# Run CLI
job-auto apply <url> [--dry-run] [--auto]
job-auto scan indeed -q "senior python engineer" --limit 20
job-auto apply-all --limit 5
job-auto review
job-auto status
job-auto browse          # Textual TUI

# Lint
ruff check src/
ruff format src/

# Type check
mypy src/

# Tests (no tests exist yet; infrastructure is in place)
pytest
pytest tests/path/to/test.py::test_name   # single test
```

## Architecture

The pipeline flows: **scan → DB → tailor (Claude) → review → submit (Playwright) → self-heal on failure**.

### Key entry points
- `pipeline.py` — `Pipeline` class orchestrates all steps; `apply(url)` is the main path
- `cli.py` — Click commands; all async code crosses into sync via `asyncio.run()` here

### Config
`config.py` has a `_LazyConfig` proxy so importing modules never crashes without a `.env`. Access it via the module-level `config` singleton. Two AI models: `claude-opus-4-6` (tailoring) and `claude-sonnet-4-6` (error analysis).

### DB layer
SQLAlchemy + SQLite at `storage/jobs.db` (WAL mode). Pure Pydantic domain models (`models/`) are separate from ORM models (`db/models.py`). `db/repository.py` converts between them. Always use `with get_session() as session:` — never manage sessions manually.

### Ingestion
`ingestion/generic.py:get_scraper(url)` is the factory. LinkedIn/Nodesk use plain `httpx`; Indeed uses Playwright (Cloudflare protection). Scrapers are async context managers. The `parse(url)` method is for single postings; `search(...)` scrapes a results page and calls `parse()` on each card URL.

### AI tailoring
Prompt templates live in `ai/prompts/*.md` with `{placeholder}` substitutions. **Do not use `str.format()`** on these templates — the JSON schema examples in the templates contain literal `{...}` braces that break it. Use the `_render(template, **kwargs)` helper defined in each AI module (it does `str.replace` for each key).

### Automation (Playwright bots)
`automation/base.py:AbstractApplicator` runs procedure steps from the knowledge base. On step failure it calls `_self_heal()`: takes a screenshot + DOM snapshot → `ai/error_analyzer.py` → Claude suggests a corrective action → applies it → retries. Max retries is `config.max_retries` (default 3). Concrete subclasses (`linkedin.py`, `indeed.py`, `nodesk.py`) implement `load_procedure()` and `execute_step()`.

### Knowledge base
`storage/knowledge_base.json` stores per-board procedures, learned selector patches, and AI notes. Access via the `kb_store` singleton in `knowledge_base/store.py`. Thread-safe. On success `updater.record_success()` is called; on failure `updater.record_failure()` and optionally `updater.record_patch()` for the corrective selector.

### Deduplication
Pipeline `apply()` looks up jobs by URL (not ID) using `repo.get_job_by_url()`. Freshly-scraped jobs always get new UUIDs, so the DB canonical record must be loaded by URL to get the correct ID before looking up applications.

### StrEnum compatibility
Python 3.10 doesn't have `StrEnum` in stdlib. It's defined locally in `models/application.py` as `class StrEnum(str, Enum)`.

## Data files
- `data/resume.md` — base resume fed to Claude for tailoring
- `data/criteria.yaml` — target titles, keywords, salary floor (used by `scan` criteria filtering)
- `storage/` — all runtime data (gitignored): SQLite DB, PDFs, screenshots, knowledge base JSON
