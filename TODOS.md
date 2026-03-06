# TODO

## TODO-002 — Default (no-tailor) application flow

### Summary

Make the untailored flow the default. Tailoring (Claude resume rewrite + cover letter) becomes
opt-in via a `--tailor` flag. This eliminates AI cost and latency for every application and lets
the user fire off applications quickly using the base resume from `data/resume.yaml`.

### Desired behaviour

| Flag | Resume | Cover letter |
|------|--------|--------------|
| *(default)* | Base resume PDF, generated from `data/resume.yaml` | None |
| `--tailor` | Claude-tailored resume PDF | Claude-generated cover letter PDF |

### Changes required

**`pipeline.py`**
- Add a `tailor: bool = False` parameter to `Pipeline.__init__` (or pass it through to `apply` /
  `apply_from_job` / `apply_all_queued`).
- Extract a new `_prepare_default_documents(job)` method that:
  1. Builds the base-resume PDF path (`storage/resumes/base_resume.pdf`).
  2. Regenerates it via `yaml_to_pdf(data/resume.yaml)` only when the YAML is newer than the
     cached PDF (compare `mtime`).
  3. Creates the `ApplicationRecord` with `resume_path` set to the cached PDF and
     `cover_letter_path = None`. Sets status to `REVIEW_PENDING` or `QUEUED` directly
     (no `TAILORING` status transition).
- In `apply()` and `apply_from_job()`, branch on the tailor flag:
  - `tailor=True` → existing `_tailor()` + `_render_pdfs()` path (unchanged)
  - `tailor=False` → new `_prepare_default_documents()` path

**`cli.py`**
- Add `--tailor` / `-t` boolean flag (default `False`) to the `apply` and `apply-all` commands.
- Pass the flag through to `Pipeline`.

**`review/diff_display.py`**
- The review UI currently shows a diff against the base resume. In the no-tailor flow there is
  nothing to diff. Display the filename of the resume being used instead of a diff when
  `app.tailored_resume_text is None`.

**`models/application.py`**
- No schema changes required; `tailored_resume_text` and `cover_letter_text` are already
  `Optional[str]` and can be `None`.

### Notes
- The cached `base_resume.pdf` lives in `storage/` (gitignored) so it is never checked in.
- `apply-all` and `submit-queued` should respect the same flag / default.
- The `--tailor` flag does not affect `scan` (scan never applies).

---

## TODO-004 — Fix resource leak in `LinkedInPlaywrightScraper.__aenter__`

**File:** `src/job_auto/ingestion/linkedin_playwright.py` — `__aenter__` (lines 48–70)

By the time `applicator.login()` is called, three resources have already been registered on `self._stack`: the Playwright process (`async_playwright()`), the browser context (`browser_context(...)`), and the page (`new_page(context)`). `AsyncExitStack` only cleans these up when `__aexit__` is called — which Python skips entirely if `__aenter__` raises.

The `try/except RuntimeError` on lines 62–66 handles the explicit contract from `LinkedInApplicator`, but `login()` navigates pages and waits on selectors, which can raise `playwright.async_api.Error`, `asyncio.TimeoutError`, or `playwright._impl._errors.TargetClosedError`. None of those are `RuntimeError`, so they propagate uncaught and the Playwright browser process and context objects leak.

**Fix:** Wrap the entire `__aenter__` body in a `try/except BaseException` that calls `await self._stack.aclose()` before re-raising:

```python
async def __aenter__(self) -> "LinkedInPlaywrightScraper":
    try:
        ...setup...
    except BaseException:
        await self._stack.aclose()
        raise
```

---

## TODO-003 — Remove `button.apply-button` from Easy Apply selector list

**File:** `src/job_auto/ingestion/linkedin.py` — `extract_job()` (line 93)

The Easy Apply detection selector list contains `"button.apply-button"`, which is a generic class LinkedIn applies to apply buttons regardless of apply type — including external-apply buttons that route to a company's ATS. This causes false positives where jobs are incorrectly marked `easy_apply_available=True`.

The other three selectors correctly cover both page variants:
- `[data-tracking-control-name*='apply-link-onsite']` — public pages (Easy Apply signal only)
- `button[aria-label*='Easy Apply']` — authenticated pages
- `button.jobs-apply-button--top-card` — authenticated pages

`button.apply-button` is not documented in the inline comment (lines 85–89) and does not correspond to either the public-page or authenticated-page strategy described there.

**Fix:** Remove `"button.apply-button, "` from the selector string. The remaining three selectors are sufficient.

---

## TODO-001 — Add fallback in `login()` when security-code typing fails

**File:** `src/job_auto/automation/linkedin.py` — `LinkedInApplicator.login()`

If Gmail returns a security code but the subsequent `human_type()` call fails
(element missing, timeout, etc.), the exception propagates uncaught and crashes
the program. The manual fallback in `_fetch_security_code()` only covers the
case where Gmail cannot return a code — it does not fire when typing fails.

**Fix:** Wrap the `human_type()` block in a try/except. On failure, fall back
to the manual prompt (print instructions and `await asyncio.to_thread(input, ...)`).
