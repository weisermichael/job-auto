# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (editable mode)
pip install -e .
playwright install chromium

# Run CLI
job-auto apply <url> [--dry-run] [--auto] [--tailor]
job-auto scan indeed -q "senior python engineer" --limit 20
job-auto scan linkedin -q "backend engineer" --auth-flow --easy-apply
job-auto apply-all --limit 5 [--tailor]
job-auto submit-queued --limit 10
job-auto retry-failed [--auto]
job-auto answer-questions [--board linkedin] [--retry]
job-auto retry-needs-answers
job-auto jobs [--unapplied] [--board linkedin] [--id <job-id>]
job-auto review
job-auto status [-s submitted]
job-auto browse          # Textual TUI
job-auto mode auto|review
job-auto kb show [-b linkedin]
job-auto gmail-auth      # one-time OAuth setup for security-code fetching

# Lint
ruff check src/
ruff format src/

# Type check
mypy src/

# Tests (no tests exist yet; infrastructure is in place)
pytest
pytest tests/path/to/test.py::test_name
```

## Architecture

The pipeline flows: **scan → DB → (tailor) → review → submit (Playwright) → self-heal on failure**.

### Key entry points
- `pipeline.py` — `Pipeline` class orchestrates all steps; `apply(url)` is the main path
- `cli.py` — Click commands; all async code crosses into sync via `asyncio.run()` here

### Config
`config.py` has a `_LazyConfig` proxy so importing modules never crashes without a `.env`. Access it via the module-level `config` singleton. Key `.env` fields: `ANTHROPIC_API_KEY`, `LINKEDIN_EMAIL`, `LINKEDIN_PASSWORD`, `CANDIDATE_NAME`, `AUTONOMOUS_MODE`, `SCAN_EASY_APPLY_ONLY`. Two AI models: `claude-opus-4-6` (tailoring, `config.tailor_model`) and `claude-sonnet-4-6` (error analysis, `config.fast_model`).

### DB layer
SQLAlchemy + SQLite at `storage/jobs.db` (WAL mode). Pure Pydantic domain models (`models/`) are separate from ORM models (`db/models.py`). `db/repository.py` converts between them. Always use `with get_session() as session:` — never manage sessions manually.

### Ingestion
`ingestion/generic.py:get_scraper(url)` is the factory. LinkedIn/Nodesk use plain `httpx`; Indeed uses Playwright (Cloudflare protection). `ingestion/linkedin_playwright.py` is a separate Playwright-based scraper for authenticated LinkedIn search (enables server-side Easy Apply filtering). Scrapers are async context managers. The `parse(url)` method is for single postings; `search(...)` scrapes a results page and calls `parse()` on each card URL.

### Default vs. tailored pipeline
By default (`Pipeline(tailor=False)`), the pipeline skips AI and attaches a cached base-resume PDF (`storage/resumes/base_resume.pdf`); the PDF is regenerated only when `data/resume.yaml` is newer. Pass `--tailor` / `Pipeline(tailor=True)` to run Claude tailoring and cover letter generation.

### AI tailoring
Prompt templates live in `ai/prompts/*.md` with `{placeholder}` substitutions. **Do not use `str.format()`** on these templates — the JSON schema examples in the templates contain literal `{...}` braces that break it. Use the `_render(template, **kwargs)` helper defined in each AI module (it does `str.replace` for each key).

### Automation (Playwright bots)
`automation/base.py:AbstractApplicator` runs procedure steps from the knowledge base. On step failure it retries with random back-off up to `config.max_retries` (default 3). The AI self-healing loop (`_self_heal`) is **currently disabled** (commented out at `base.py:160`). `UnansweredQuestionsError` (from `automation/easy_apply_modal.py`) is caught and parks the application at `NEEDS_ANSWERS` status. Run `job-auto answer-questions` to answer them interactively (type-aware prompts per field); answers are written to `qa_cache` and the pending entry is resolved immediately after each answer (Ctrl-C safe). Then run `job-auto retry-needs-answers` to resubmit. Concrete subclasses (`linkedin.py`, `indeed.py`, `nodesk.py`) implement `load_procedure()` and `execute_step()`.

### Application status lifecycle
`models/application.py:ApplicationStatus` states (roughly in order):
`DISCOVERED → QUEUED → TAILORING → REVIEW_PENDING → SUBMITTING → SUBMITTED`
Terminal states: `SUBMITTED`, `REVIEW_REJECTED`, `OFFER`, `WITHDRAWN`.
Retry-eligible: `FAILED` (while `failure_count < 3`), `NEEDS_ANSWERS`, `SUBMITTING` (stuck crash).

### Knowledge base
`storage/knowledge_base.json` stores per-board procedures, learned selector patches, and AI notes. Access via the `kb_store` singleton in `knowledge_base/store.py`. Thread-safe. On success `updater.record_success()` is called; on failure `updater.record_failure()` and optionally `updater.record_patch()` for the corrective selector. Pending unanswered questions are stored under `<board>.pending_questions[job_url]`; `kb_store.resolve_pending_question(board, job_url, label)` removes one answered question and drops the job URL key when the last question is resolved. `kb_store.list_all_pending_questions(board)` returns the full `{job_url: questions}` dict for a board.

### LinkedIn session
A Playwright storage-state session is saved to `storage/linkedin_session.json` after first login. Subsequent runs reuse it. `--auth-flow` on `scan` uses the authenticated Playwright scraper (`LinkedInAuthScraper`) which applies server-side Easy Apply filtering. If the session is missing or stale, `LinkedInAuthError` is raised.

### Gmail integration
`utils/gmail.py` uses OAuth2 to read security-verification emails from LinkedIn/Indeed. Run `job-auto gmail-auth` once; place `storage/gmail_credentials.json` (downloaded from Google Cloud Console) beforehand. Token saved to `storage/gmail_token.json`.

### Deduplication
Pipeline `apply()` looks up jobs by URL (not ID) using `repo.get_job_by_url()`. Freshly-scraped jobs always get new UUIDs, so the DB canonical record must be loaded by URL to get the correct ID before looking up applications.

## Data files
- `data/resume.yaml` — base resume in rendercv YAML format (`engineeringresumes` theme); replace the placeholder with your actual content
- `data/criteria.yaml` — target titles, keywords, salary floor (used by `scan` criteria filtering)
- `storage/` — all runtime data (gitignored): SQLite DB, PDFs, screenshots, knowledge base JSON
