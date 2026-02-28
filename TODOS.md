# TODO

## TODO-001 — Add fallback in `login()` when security-code typing fails

**File:** `src/job_auto/automation/linkedin.py` — `LinkedInApplicator.login()`

If Gmail returns a security code but the subsequent `human_type()` call fails
(element missing, timeout, etc.), the exception propagates uncaught and crashes
the program. The manual fallback in `_fetch_security_code()` only covers the
case where Gmail cannot return a code — it does not fire when typing fails.

**Fix:** Wrap the `human_type()` block in a try/except. On failure, fall back
to the manual prompt (print instructions and `await asyncio.to_thread(input, ...)`).
