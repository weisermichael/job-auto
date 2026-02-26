# job-auto

A personal job application automation system. Scrapes job postings from LinkedIn, Indeed, and Nodesk; uses Claude to tailor your resume and write a cover letter for each role; and submits applications via Playwright. Includes a self-healing loop that screenshots failures and asks Claude how to fix them.

Default mode requires your approval before anything gets submitted. Flip one flag to go fully autonomous.

---

## How it works

```
scan → store → tailor (Claude) → review → submit (Playwright)
                                              ↑
                                     self-heal on failure
                                     (screenshot + Claude)
```

1. **Scan** — Playwright or HTTP scraper collects job postings and stores them in a local SQLite database.
2. **Tailor** — Claude rewrites your resume to match the job description and generates a cover letter. It is explicitly instructed never to add experience you don't have.
3. **Review** — A Rich terminal diff shows exactly what changed between your base resume and the tailored version. You approve, reject, or skip each application.
4. **Submit** — Playwright fills and submits the form using a per-board procedure stored in a knowledge base. On failure it screenshots the page, sends it to Claude for analysis, applies the suggested fix, and retries.

---

## Requirements

- Python 3.10+
- An [Anthropic API key](https://console.anthropic.com/)
- Chromium (installed via Playwright)

---

## Installation

```bash
git clone <repo>
cd job-automation
pip install -e .
playwright install chromium
```

---

## Setup

**1. Create your `.env` file**

```bash
cp .env.example .env
```

Edit `.env` and fill in at minimum:

```env
ANTHROPIC_API_KEY=sk-ant-...
```

LinkedIn credentials are only needed if you want to submit Easy Apply applications:

```env
LINKEDIN_EMAIL=you@example.com
LINKEDIN_PASSWORD=yourpassword
```

**2. Add your resume**

Replace `data/resume.md` with your actual resume in Markdown format. This is the base that Claude tailors for each role — keep it accurate and complete.

**3. Configure your search criteria** *(optional)*

Edit `data/criteria.yaml`:

```yaml
titles:
  - "Senior Software Engineer"
  - "Staff Software Engineer"

keywords:
  - Python
  - distributed systems
  - AWS

salary_floor: 150000   # Annual USD, 0 to disable
remote_only: true
daily_limit: 10
```

---

## Quickstart

```bash
# Find jobs and apply to them (with review)
job-auto scan indeed -q "Site Reliability Engineer" --limit 20
job-auto apply-all --limit 5

# Apply to a single job directly
job-auto apply https://www.linkedin.com/jobs/view/...
```

---

## Commands

### `scan` — find new jobs

```bash
job-auto scan <board> [OPTIONS]
```

Scrapes the board and stores new listings in the database. Does not apply to anything yet.

| Board | Notes |
|-------|-------|
| `linkedin` | Uses LinkedIn public job search (httpx) |
| `indeed` | Uses Playwright — bypasses Cloudflare |
| `nodesk` | Nodesk.co remote job listings (httpx) |

```bash
job-auto scan indeed -q "Site Reliability Engineer"
job-auto scan linkedin -q "senior python engineer" --limit 50
job-auto scan nodesk --limit 30
job-auto scan indeed -q "DevOps" --no-remote   # include non-remote
```

---

### `apply-all` — process scanned jobs

```bash
job-auto apply-all [OPTIONS]
```

Takes all jobs in the database that haven't been processed yet, runs them through tailoring, and either queues them for your review or submits immediately.

```bash
job-auto apply-all                    # review each before submitting (default)
job-auto apply-all --auto             # skip review, submit immediately
job-auto apply-all --dry-run          # tailor + show diff, but don't submit
job-auto apply-all --limit 5          # process at most 5 jobs
```

---

### `apply` — apply to a single URL

```bash
job-auto apply <url> [OPTIONS]
```

Full pipeline for one job: scrapes the URL, tailors your resume, shows a diff for review, then submits.

```bash
job-auto apply https://www.indeed.com/jobs/view/...
job-auto apply https://www.linkedin.com/jobs/view/... --auto
job-auto apply https://nodesk.co/remote-jobs/... --dry-run
```

---

### `review` — approve pending applications

```bash
job-auto review [OPTIONS]
```

Opens an interactive terminal UI for each application waiting for your approval. For each one you'll see:

- Job summary (company, title, salary, board, URL)
- A unified diff of your base resume vs. the tailored version
- The generated cover letter

Your choices: **approve**, **reject**, **approve with notes**, or **skip for now**.

```bash
job-auto review          # work through all pending
job-auto review --list   # just list what's waiting, don't review yet
```

---

### `status` — see what's happened

```bash
job-auto status [OPTIONS]
```

```bash
job-auto status                            # last 30 applications
job-auto status --last 50
job-auto status -s submitted               # only submitted
job-auto status -s failed                  # only failed
job-auto status -s review_pending          # only waiting for review
```

Application statuses:

| Status | Meaning |
|--------|---------|
| `tailoring` | Claude is working on it |
| `review_pending` | Waiting for your approval |
| `review_rejected` | You rejected it |
| `queued` | Approved, waiting to submit |
| `submitting` | Playwright is filling the form |
| `submitted` | Done |
| `failed` | Playwright failed after all retries |
| `retry` | Queued for another attempt |

---

### `retry-failed` — resubmit failed applications

```bash
job-auto retry-failed [--auto]
```

Retries all failed applications that are under the max retry limit (default: 3 attempts).

---

### `mode` — toggle autonomous mode

```bash
job-auto mode auto      # submit without review
job-auto mode review    # require approval before submitting (default)
```

Persists the setting to your `.env` file.

---

### `kb show` — inspect the knowledge base

```bash
job-auto kb show              # all boards
job-auto kb show -b linkedin  # one board
```

The knowledge base (`storage/knowledge_base.json`) stores per-board application procedures, learned selector patches from self-healing, and notes about quirks like rate limits and CAPTCHAs.

---

## Typical workflows

**Batch apply with review:**
```bash
job-auto scan indeed -q "Platform Engineer" --limit 30
job-auto scan linkedin -q "Platform Engineer" --limit 30
job-auto apply-all --limit 10    # tailor + show diff for each
job-auto review                  # approve or reject
job-auto status                  # check results
```

**Fully autonomous (no prompts):**
```bash
job-auto scan indeed -q "Backend Engineer" --limit 20
job-auto apply-all --auto --limit 10
```

**One-off application:**
```bash
job-auto apply https://www.linkedin.com/jobs/view/1234567890 --dry-run   # preview
job-auto apply https://www.linkedin.com/jobs/view/1234567890             # for real
```

**Morning routine (cron-friendly):**
```bash
job-auto scan linkedin -q "Senior Python Engineer" --limit 50
job-auto scan indeed   -q "Senior Python Engineer" --limit 50
job-auto apply-all --auto --limit 10
job-auto status --last 20
```

---

## Configuration reference

All settings can be set in `.env` or as environment variables.

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | *(required)* | Your Anthropic API key |
| `LINKEDIN_EMAIL` | — | LinkedIn account email |
| `LINKEDIN_PASSWORD` | — | LinkedIn account password |
| `AUTONOMOUS_MODE` | `false` | Skip human review before submitting |
| `MAX_RETRIES` | `3` | Self-healing retry attempts per application |
| `DAILY_APPLY_LIMIT` | `10` | Max submissions per day |
| `HEADLESS_BROWSER` | `true` | Set `false` to watch Playwright in action |
| `STORAGE_DIR` | `storage` | Where the database, PDFs, and screenshots live |
| `DATA_DIR` | `data` | Where your resume and criteria live |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LOG_FILE` | `storage/job_auto.log` | Log file path |

---

## Project layout

```
job-automation/
├── data/
│   ├── resume.md          ← your base resume (edit this)
│   └── criteria.yaml      ← search preferences
│
├── storage/               ← runtime data (gitignored)
│   ├── jobs.db            ← SQLite: all jobs and applications
│   ├── knowledge_base.json← learned per-board procedures
│   ├── resumes/           ← tailored resume PDFs
│   ├── cover_letters/     ← cover letter PDFs
│   └── screenshots/       ← failure screenshots for debugging
│
└── src/job_auto/
    ├── cli.py             ← all CLI commands
    ├── pipeline.py        ← main orchestrator
    ├── config.py          ← settings
    ├── ai/                ← Claude: tailoring, cover letters, failure analysis
    ├── automation/        ← Playwright bots (LinkedIn, Indeed, Nodesk)
    ├── ingestion/         ← scrapers (LinkedIn, Indeed, Nodesk, generic)
    ├── db/                ← SQLAlchemy ORM + repository
    ├── knowledge_base/    ← procedure store + learned patches
    ├── review/            ← terminal diff UI + approval queue
    └── utils/             ← logging, rate limiter, PDF converter
```

---

## Notes and limits

**LinkedIn** — Easy Apply is rate-limited to roughly 10 applications per day before LinkedIn flags activity. The daily limit defaults to 10 for this reason. LinkedIn credentials are required for form submission but not for scanning public listings.

**Indeed** — The HTML search endpoint and RSS feed are both behind Cloudflare. The Indeed scraper uses Playwright (a real browser) to bypass this, so scanning is slower than other boards.

**CAPTCHAs** — If a CAPTCHA appears mid-application, the self-healing loop will detect it (via screenshot + Claude) and pause, notifying you to solve it manually before the bot retries.

**Credentials** — Your `.env` file is gitignored. Passwords are stored as `pydantic.SecretStr` and never written to logs.

**AI accuracy** — Claude is prompted with an explicit guardrail: *"Never add technologies or experience the candidate doesn't have."* The review step exists as a second check. Always review tailored content before enabling `--auto`.
