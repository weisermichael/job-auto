# Bug Tracker

---

## BUG-001 — Application Stuck in `SUBMITTING` Status After Playwright Exception

**Status:** Open
**Affected file:** `src/job_auto/pipeline.py` — `Pipeline._submit()`

### Description

When `_submit()` raises an uncaught exception during the Playwright phase (e.g., a browser timeout, missing session file, or network error), the application record is permanently left in `SUBMITTING` status. The method writes `SUBMITTING` to the database before launching Playwright, but the final `repo.update_application()` call — which would set the status to either `SUBMITTED` or `FAILED` — is never reached when an exception propagates out of the method. The caller (`submit_queued()`, `apply()`, etc.) does not perform any status recovery either.

The application effectively becomes orphaned: it no longer appears in `submit-queued` runs (which filter for `QUEUED`) and is not marked as a retriable failure.

### Steps to Reproduce

1. Run `job-auto apply <url> --dry-run` to create a `QUEUED` application.
2. Ensure the Playwright submission will fail — e.g., no LinkedIn session file, or a URL that times out.
3. Run `job-auto submit-queued`.
4. Observe the logged error: `submit_queued_error ... Timeout 30000ms exceeded`.
5. Run `job-auto status`.

### Expected Result

The application status is set to `FAILED` with the exception message stored in `last_failure_reason`. The application is visible as a failed, retriable record in `job-auto status`.

### Actual Result

The application status is `submitting` in the database. It is excluded from future `submit-queued` runs and does not appear as a failure. The only evidence of the error is the log line.

### Root Cause

`_submit()` in `pipeline.py` (line ~337) writes `SUBMITTING` to the DB before entering the Playwright context managers. If any exception is raised inside those context managers, execution skips the final `repo.update_application()` call at the bottom of the method. There is no `try/finally` to guarantee the status is resolved.

### Proposed Fix

Wrap the Playwright block in a `try/except/finally` so the DB write always occurs:

```python
try:
    async with async_playwright() as pw:
        ...
except Exception:
    app.status = ApplicationStatus.FAILED
    app.last_failure_reason = "Unhandled exception during submission"
    raise
finally:
    with get_session() as session:
        repo.update_application(session, app)
```

---

## BUG-002 — LinkedIn Security Code Typed Into Wrong Selector

**Status:** Open
**Affected file:** `src/job_auto/automation/linkedin.py` — `LinkedInApplicator.login()`

### Description

`login()` builds a comma-separated selector list for the security-code input field (`input[name='pin'], input[id*='verification'], input[id*='code']`) and passes it to `page.wait_for_selector()`, which resolves as soon as **any** of the three candidates appears in the DOM. However, the subsequent `human_type()` call always uses `pin_sel.split(",")[0].strip()` — i.e., unconditionally `input[name='pin']` — regardless of which selector Playwright actually found.

If LinkedIn's challenge page renders `input[id*='verification']` or `input[id*='code']` instead of `input[name='pin']`, the wait succeeds but the type fails because the hardcoded first selector does not exist on the page.

### Steps to Reproduce

1. Trigger a LinkedIn security challenge (log in from a new IP / device).
2. Ensure LinkedIn's challenge page uses an input with `id` containing `verification` or `code` rather than `name='pin'`.
3. Run `job-auto apply <url>`.
4. Observe `human_type` raising a timeout or element-not-found error on `input[name='pin']`.

### Expected Result

The security code is typed into whichever input field is actually present on the page.

### Actual Result

`human_type` always targets `input[name='pin']`. If that element is absent, the login step fails.

### Root Cause

`linkedin.py` line ~207:
```python
pin_sel = "input[name='pin'], input[id*='verification'], input[id*='code']"
await self.page.wait_for_selector(pin_sel, timeout=5000)
await human_type(self.page, pin_sel.split(",")[0].strip(), code)  # always first
```
`wait_for_selector` and `human_type` use different selector resolution strategies: the former accepts any match from the list; the latter acts only on the literal first entry.

### Proposed Fix

Use `_smart_fill()` (which already iterates comma-separated selector fallbacks) instead of the hardcoded split:

```python
await self._smart_fill(pin_sel, code)
```

---

## BUG-003 — Self-Heal Crashes: `datetime` Not JSON Serializable

**Status:** Open
**Affected file:** `src/job_auto/automation/base.py` — `AbstractApplicator._self_heal()`

### Description

Every time a Playwright step fails and self-heal is triggered, `_self_heal()` calls
`self._procedure.model_dump()` to produce a dict for `analyze_failure()`.
`ApplicationProcedure.last_updated` is a `datetime` field; `model_dump()` returns it as a
Python `datetime` object. `analyze_failure()` then calls `json.dumps(procedure, indent=2)`
(`error_analyzer.py:48`), which raises `TypeError: Object of type datetime is not JSON serializable`.

The exception is caught by the `except Exception` in `_self_heal()`, logged as
`self_heal_failed`, and `None` is returned — meaning no corrective action is ever applied.
Self-healing is completely inoperative as a result.

### Steps to Reproduce

1. Run `job-auto apply --auto <url>` against a job where a step fails (e.g. Easy Apply button
   selector mismatch).
2. Observe three `self_heal_failed` log lines with
   `error=Object of type datetime is not JSON serializable`.
3. Observe `status=failed` with no corrective action attempted.

### Expected Result

`_self_heal()` serialises the procedure dict to JSON successfully, sends it to Claude, and
returns a corrective action that is applied before the next retry.

### Actual Result

Every self-heal attempt crashes with a `TypeError`. No corrective action is applied. All
retries fail identically. Application is marked `FAILED`.

### Root Cause

`base.py` line ~163:
```python
procedure_dict = self._procedure.model_dump()   # datetime objects not JSON-safe
```

### Proposed Fix

Use Pydantic v2's JSON-serialization mode, which converts `datetime` → ISO 8601 string:

```python
procedure_dict = self._procedure.model_dump(mode="json")
```

---

## BUG-004 — Self-Heal `_apply_correction` Crashes Uncaught, Killing the Process

**Status:** Open
**Affected file:** `src/job_auto/automation/base.py` — `AbstractApplicator._run_step_with_healing()`

### Description

When a step fails and self-heal returns a corrective action, `_apply_correction()` is called
**inside the `except` block but not wrapped in its own try/except**. If the corrective action
itself raises (e.g., a Playwright 30 s timeout on the suggested click selector), the exception
propagates uncaught all the way out of `_run_step_with_healing` → `submit` → `pipeline.apply`,
where it surfaces as a bare "Unexpected error" traceback instead of a recorded `FAILED`
application.

### Steps to Reproduce

1. Run `job-auto apply --auto <url>` against a job where step 2 (click Easy Apply) fails.
2. Self-heal is triggered; Claude returns a corrective `click` action with a broad selector.
3. `_apply_correction` calls `await self.page.click(selector)` with Playwright's default 30 s
   timeout.
4. The click times out → `PlaywrightTimeoutError` raised inside the `except` block.
5. Exception propagates uncaught; CLI prints "Unexpected error" and exits with code 1.
6. The application record is left in `SUBMITTING` (see also BUG-001).

### Expected Result

The timeout from `_apply_correction` is caught; the attempt is logged as a failure; the retry
loop continues or the application is marked `FAILED` through the normal path.

### Actual Result

The process crashes with a raw `PlaywrightTimeoutError` traceback. No failure is recorded in
the DB via the normal path; the application is orphaned in `SUBMITTING` status.

### Root Cause

`base.py` lines ~142-146:
```python
except (PlaywrightTimeout, Exception) as e:
    ...
    if correction:
        await self._apply_correction(correction, context)  # not in a try/except
    await asyncio.sleep(random.uniform(1, 3))
```
Any exception raised by `_apply_correction` escapes the loop entirely.

### Proposed Fix

Wrap the call in its own try/except and log + continue on failure:

```python
if correction:
    try:
        await self._apply_correction(correction, context)
    except Exception as heal_err:
        logger.warning("self_heal_correction_failed", error=str(heal_err)[:200])
```
