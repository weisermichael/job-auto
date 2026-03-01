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

## TODO-001 — Add fallback in `login()` when security-code typing fails

**File:** `src/job_auto/automation/linkedin.py` — `LinkedInApplicator.login()`

If Gmail returns a security code but the subsequent `human_type()` call fails
(element missing, timeout, etc.), the exception propagates uncaught and crashes
the program. The manual fallback in `_fetch_security_code()` only covers the
case where Gmail cannot return a code — it does not fire when typing fails.

**Fix:** Wrap the `human_type()` block in a try/except. On failure, fall back
to the manual prompt (print instructions and `await asyncio.to_thread(input, ...)`).
