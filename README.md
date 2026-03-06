# job-auto

A personal job application automation system. Scrapes job postings from LinkedIn, Indeed, and Nodesk; optionally uses Claude to tailor your resume and write a cover letter for each role; and submits applications via Playwright. Includes a self-healing loop that screenshots failures and asks Claude how to fix them.

Default mode requires your approval before anything gets submitted. Flip one flag to go fully autonomous.

---

## How it works

```
scan → store → [tailor (Claude)] → review → submit (Playwright)
                                                 ↑
                                        self-heal on failure
                                        (screenshot + Claude)
```

1. **Scan** — Playwright or HTTP scraper collects job postings and stores them in a local SQLite database.
2. **Tailor** *(optional, `--tailor`)* — Claude rewrites your resume to match the job description and generates a cover letter. It is explicitly instructed never to add experience you don't have. Without `--tailor`, the base resume PDF is used directly.
3. **Review** — A Rich terminal diff shows exactly what changed between your base resume and the tailored version (or the base resume in non-tailor mode). You approve, reject, or skip each application.
4. **Submit** — Playwright fills and submits the form using a per-board procedure stored in a knowledge base. On failure it retries with back-off; a Claude-guided self-healing loop is implemented but currently disabled pending stability work.

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
CANDIDATE_NAME=Your Name
```

LinkedIn credentials are only needed if you want to submit Easy Apply applications:

```env
LINKEDIN_EMAIL=you@example.com
LINKEDIN_PASSWORD=yourpassword
```

**2. Add your resume**

Replace `data/resume.yaml` with your actual resume in rendercv YAML format (`engineeringresumes` theme). This is the base that Claude tailors for each role when `--tailor` is used — keep it accurate and complete.

**3. Set up Gmail for automatic LinkedIn security codes** *(optional but recommended)*

LinkedIn frequently sends a 6-digit verification code to your email when it detects a fresh login. Without this step the run will pause and ask you to enter the code manually. With it, the code is fetched and filled in automatically.

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a project.
2. Enable the **Gmail API** for that project (APIs & Services → Library → search "Gmail API").
3. Create OAuth2 credentials: APIs & Services → Credentials → Create Credentials → **OAuth client ID** → Application type: **Desktop app**. Download the JSON file.
4. Add your Gmail address as a test user: OAuth consent screen → **Test users** → **+ Add Users** → enter your Gmail address → Save. Skip this and you'll get a 403 access_denied when authorizing.
5. Place the downloaded file at `storage/gmail_credentials.json`.
6. Run the one-time authorization:

```bash
job-auto gmail-auth
```

A browser window opens. Sign in and grant read-only Gmail access. The token is saved to `storage/gmail_token.json` and reused on every subsequent run. Both files are covered by the `storage/` gitignore entry — they never get committed.

**4. Configure your search criteria** *(optional)*

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
# Find jobs and apply to them (with review, no tailoring)
job-auto scan indeed -q "Site Reliability Engineer" --limit 20
job-auto apply-all --limit 5

# Same, but tailor resume and generate cover letter for each job
job-auto apply-all --limit 5 --tailor

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
| `linkedin` | HTTP scraper (httpx); add `--auth-flow` for authenticated Playwright with server-side Easy Apply filtering |
| `indeed` | Uses Playwright — bypasses Cloudflare |
| `nodesk` | Nodesk.co remote job listings (httpx) |

```bash
job-auto scan indeed -q "Site Reliability Engineer"
job-auto scan linkedin -q "senior python engineer" --limit 50
job-auto scan nodesk --limit 30
job-auto scan indeed -q "DevOps" --no-remote           # include non-remote
job-auto scan linkedin -q "backend engineer" --easy-apply            # post-filter: Easy Apply only
job-auto scan linkedin -q "backend engineer" --auth-flow --easy-apply  # server-side filter (more reliable)
```

---

### `apply-all` — process scanned jobs

```bash
job-auto apply-all [OPTIONS]
```

Takes all jobs in the database that haven't been processed yet and either queues them for your review or submits immediately.

```bash
job-auto apply-all                    # review each before submitting (default)
job-auto apply-all --tailor           # tailor resume + generate cover letter for each
job-auto apply-all --auto             # skip review, submit immediately
job-auto apply-all --dry-run          # prepare documents but don't submit
job-auto apply-all --limit 5          # process at most 5 jobs
```

---

### `apply` — apply to a single URL

```bash
job-auto apply <url> [OPTIONS]
```

Full pipeline for one job: scrapes the URL, prepares documents, shows a diff for review, then submits.

```bash
job-auto apply https://www.indeed.com/jobs/view/...
job-auto apply https://www.linkedin.com/jobs/view/... --tailor  # tailor resume
job-auto apply https://www.linkedin.com/jobs/view/... --auto    # skip review
job-auto apply https://nodesk.co/remote-jobs/... --dry-run
```

---

### `jobs` — list jobs in the database

```bash
job-auto jobs [OPTIONS]
```

```bash
job-auto jobs                          # all recent jobs (last 50)
job-auto jobs --unapplied              # only jobs not yet applied to
job-auto jobs --board indeed           # filter by board
job-auto jobs --id a1b2c3d4e5f6        # full detail including description
```

---

### `browse` — interactive TUI

```bash
job-auto browse [--unapplied] [--board linkedin|indeed|nodesk|all]
```

A Textual terminal UI for browsing and managing the job database. Keys: `↑`/`↓` navigate, `Enter` expand/collapse description, `d` delete, `u` toggle unapplied filter, `r` refresh, `q`/`Esc` quit.

---

### `review` — approve pending applications

```bash
job-auto review [OPTIONS]
```

Opens an interactive terminal UI for each application waiting for your approval. For each one you'll see:

- Job summary (company, title, salary, board, URL)
- A unified diff of your base resume vs. the tailored version (when `--tailor` was used)
- The generated cover letter (when `--tailor` was used)

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
| `needs_answers` | Required form questions couldn't be auto-answered; add answers to `storage/knowledge_base.json` under `<board> → qa_cache` then re-run |
| `retry` | Queued for another attempt |

---

### `submit-queued` — submit approved applications

```bash
job-auto submit-queued [--limit N]
```

Submits all applications in `queued` status (approved by review but not yet submitted). Useful after running `apply-all --dry-run` or after manually approving via `review`.

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

### `gmail-auth` — authorize Gmail for security code fetching

```bash
job-auto gmail-auth
```

One-time OAuth2 setup. Requires `storage/gmail_credentials.json` to exist first (see [Setup](#setup) above). Opens a browser for consent and saves the token to `storage/gmail_token.json`.

Once authorized, LinkedIn security challenges are handled automatically: the bot polls Gmail for the verification email, extracts the 6-digit code, and fills it in without any manual input. If the token is missing or polling times out, it falls back to a terminal prompt.

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
job-auto apply-all --limit 10    # prepare documents, show diff for each
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
| `CANDIDATE_NAME` | — | Your name, used in cover letter generation |
| `LINKEDIN_EMAIL` | — | LinkedIn account email |
| `LINKEDIN_PASSWORD` | — | LinkedIn account password |
| `AUTONOMOUS_MODE` | `false` | Skip human review before submitting |
| `SCAN_EASY_APPLY_ONLY` | `false` | Globally filter to Easy Apply jobs on every scan |
| `MAX_RETRIES` | `3` | Retry attempts per failed application step |
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
│   ├── resume.yaml        ← your base resume (rendercv YAML format, edit this)
│   └── criteria.yaml      ← search preferences
│
├── storage/               ← runtime data (gitignored)
│   ├── jobs.db            ← SQLite: all jobs and applications
│   ├── knowledge_base.json← learned per-board procedures
│   ├── linkedin_session.json← saved LinkedIn cookies (auto-managed)
│   ├── gmail_credentials.json← Google OAuth2 client secrets (you provide)
│   ├── gmail_token.json   ← Gmail access token (created by gmail-auth)
│   ├── resumes/           ← base and tailored resume PDFs
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

**LinkedIn security challenges** — LinkedIn treats fresh browser sessions as suspicious and often sends a 6-digit verification code to your email. The session is saved to `storage/linkedin_session.json` after the first successful login, so subsequent runs skip the challenge entirely. When a challenge does appear, the bot first tries to fetch the code from Gmail automatically (requires `gmail-auth` setup); if that isn't configured or times out, it pauses and prompts you to enter the code manually.

**Indeed** — The HTML search endpoint and RSS feed are both behind Cloudflare. The Indeed scraper uses Playwright (a real browser) to bypass this, so scanning is slower than other boards.

**Credentials** — Your `.env` file is gitignored. Passwords are stored as `pydantic.SecretStr` and never written to logs.

**AI accuracy** — Claude is prompted with an explicit guardrail: *"Never add technologies or experience the candidate doesn't have."* The review step exists as a second check. Always review tailored content before enabling `--auto`.
