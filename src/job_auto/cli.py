"""Click CLI entry point for job-auto."""

from __future__ import annotations

import asyncio
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from job_auto.config import config
from job_auto.utils.logging import configure_logging

console = Console()


def _setup_logging() -> None:
    configure_logging(level=config.log_level, log_file=config.log_file)


@click.group()
@click.version_option(version="0.1.0", prog_name="job-auto")
def cli() -> None:
    """Job Application Automation System.

    \b
    Quick start:
      job-auto apply <url>            Apply to a single job (review mode)
      job-auto apply <url> --auto     Apply without human review
      job-auto scan linkedin          Find new LinkedIn jobs
      job-auto jobs                   List jobs in the database
      job-auto browse                 Interactive TUI job browser
      job-auto apply-all              Apply to all scanned-but-unapplied jobs
      job-auto review                 Review pending applications
      job-auto status                 Show recent application history
    """
    _setup_logging()


# ──────────────────────────────────────────────────────────
# apply
# ──────────────────────────────────────────────────────────

@cli.command()
@click.argument("url")
@click.option("--auto", is_flag=True, help="Skip human review and submit immediately")
@click.option("--dry-run", is_flag=True, help="Run pipeline without submitting the form")
@click.option("--tailor", "-t", is_flag=True, help="Use Claude to tailor resume and generate cover letter")
def apply(url: str, auto: bool, dry_run: bool, tailor: bool) -> None:
    """Apply to a single job posting at URL."""
    from job_auto.pipeline import Pipeline, PipelineError

    pipeline = Pipeline(autonomous=auto or config.autonomous_mode, tailor=tailor)

    async def _run():
        try:
            app = await pipeline.apply(url, dry_run=dry_run)
            console.print(f"\n[bold green]✓ Done[/bold green]  status=[cyan]{app.status}[/cyan]  id={app.id}")
            from job_auto.models.application import ApplicationStatus
            if app.status == ApplicationStatus.NEEDS_ANSWERS:
                console.print(
                    "\n[yellow]Some required questions could not be answered automatically.[/yellow]\n"
                    "  1. Find the question labels in [bold]storage/knowledge_base.json[/bold] "
                    "under [bold]linkedin → pending_questions[/bold].\n"
                    "  2. Add answers under [bold]linkedin → qa_cache[/bold] "
                    "(key = exact question text, value = your answer).\n"
                    f"  3. Re-run: [bold]job-auto apply {url}[/bold]"
                )
        except PipelineError as e:
            console.print(f"[bold red]Pipeline error:[/bold red] {e}")
            raise SystemExit(1)
        except Exception as e:
            console.print(f"[bold red]Unexpected error:[/bold red] {e}")
            raise

    asyncio.run(_run())


# ──────────────────────────────────────────────────────────
# apply-all
# ──────────────────────────────────────────────────────────

@cli.command("apply-all")
@click.option("--limit", "-n", default=10, show_default=True, help="Max jobs to process")
@click.option("--auto", is_flag=True, help="Skip human review before submitting")
@click.option("--dry-run", is_flag=True, help="Prepare and review but do not submit forms")
@click.option("--tailor", "-t", is_flag=True, help="Use Claude to tailor resume and generate cover letter")
def apply_all(limit: int, auto: bool, dry_run: bool, tailor: bool) -> None:
    """Apply to all scanned-but-unapplied jobs stored in the database.

    \b
    Typical workflow:
      job-auto scan indeed -q "Site Reliability Engineer"
      job-auto apply-all --limit 5          # review each before submitting
      job-auto apply-all --limit 5 --auto   # submit without review
      job-auto apply-all --limit 5 --tailor # tailor resume for each job
    """
    from job_auto.pipeline import Pipeline, PipelineError

    pipeline = Pipeline(autonomous=auto or config.autonomous_mode, tailor=tailor)

    async def _run():
        apps = await pipeline.apply_all_queued(limit=limit, dry_run=dry_run)

        if not apps:
            console.print("[yellow]No unapplied jobs found in the database.[/yellow]")
            console.print("Run [bold]job-auto scan <board> -q <query>[/bold] first.")
            return

        table = Table(title=f"Results ({len(apps)} processed)", border_style="green")
        table.add_column("App ID", style="dim")
        table.add_column("Job ID", style="dim")
        table.add_column("Status")

        status_colors = {
            "submitted": "green",
            "queued": "cyan",
            "review_pending": "yellow",
            "review_rejected": "red",
            "failed": "red",
        }
        for app in apps:
            color = status_colors.get(app.status.value, "white")
            table.add_row(app.id, app.job_id, f"[{color}]{app.status.value}[/{color}]")

        console.print(table)

        submitted = sum(1 for a in apps if a.status.value == "submitted")
        pending = sum(1 for a in apps if a.status.value == "review_pending")
        if pending:
            console.print(
                f"\n[yellow]{pending} application(s) waiting for review.[/yellow] "
                "Run [bold]job-auto review[/bold] to approve them."
            )
        if submitted:
            console.print(f"[green]{submitted} application(s) submitted.[/green]")

    asyncio.run(_run())


# ──────────────────────────────────────────────────────────
# jobs
# ──────────────────────────────────────────────────────────

@cli.command("jobs")
@click.option("--last", "-n", default=50, show_default=True, help="Max jobs to show")
@click.option("--board", "-b", default=None, help="Filter by board (linkedin|indeed|nodesk)")
@click.option("--unapplied", is_flag=True, help="Show only jobs with no application yet")
@click.option("--id", "job_id", default=None, help="Show full detail for one job ID")
def jobs(last: int, board: Optional[str], unapplied: bool, job_id: Optional[str]) -> None:
    """List jobs stored in the database.

    \b
    Examples:
      job-auto jobs                        # all recent jobs
      job-auto jobs --unapplied            # only jobs not yet applied to
      job-auto jobs --board indeed         # filter by board
      job-auto jobs --id a1b2c3d4e5f6      # full detail for one job
    """
    from job_auto.db import repository as repo
    from job_auto.db.session import get_session

    # ── detail view ──────────────────────────────────────────
    if job_id:
        with get_session() as session:
            job = repo.get_job(session, job_id)
        if not job:
            console.print(f"[red]Job {job_id!r} not found.[/red]")
            raise SystemExit(1)

        console.print(f"\n[bold]{job.title}[/bold] — {job.company}")
        console.print(f"[dim]{job.board.value.upper()}  ·  {job.url_str}[/dim]")
        if job.salary_range:
            console.print(f"Salary: {job.salary_range}")
        console.print(f"Remote: {'Yes' if job.remote else 'No'}  |  "
                      f"Easy Apply: {'Yes' if job.easy_apply_available else 'No'}  |  "
                      f"Level: {job.experience_level.value}")
        if job.tech_stack:
            console.print(f"Tech: {', '.join(job.tech_stack)}")
        console.print(f"Found: {job.date_found.strftime('%Y-%m-%d %H:%M UTC')}\n")
        console.print("[bold]Description[/bold]")
        console.print(job.description[:3000] + ("…" if len(job.description) > 3000 else ""))
        return

    # ── list view ────────────────────────────────────────────
    with get_session() as session:
        if unapplied:
            all_jobs = repo.list_unapplied_jobs(session, limit=last)
        else:
            all_jobs = repo.list_jobs(session, limit=last)

    if board:
        all_jobs = [j for j in all_jobs if j.board.value == board.lower()]

    if not all_jobs:
        msg = "No jobs found."
        if unapplied:
            msg += " Run [bold]job-auto scan <board> -q <query>[/bold] first."
        console.print(f"[yellow]{msg}[/yellow]")
        return

    title = "Unapplied Jobs" if unapplied else "Jobs"
    if board:
        title += f" ({board})"
    title += f" — {len(all_jobs)} total"

    table = Table(title=title, border_style="cyan")
    table.add_column("ID", style="dim")
    table.add_column("Board")
    table.add_column("Company", style="bold")
    table.add_column("Title")
    table.add_column("Remote", justify="center")
    table.add_column("Easy Apply", justify="center")
    table.add_column("Salary")
    table.add_column("Found")

    for job in all_jobs:
        table.add_row(
            job.id,
            job.board.value,
            job.company[:28],
            job.title[:45],
            "✓" if job.remote else "",
            "✓" if job.easy_apply_available else "",
            job.salary_range or "",
            job.date_found.strftime("%m-%d %H:%M"),
        )

    console.print(table)
    console.print(f"[dim]Use [bold]job-auto jobs --id <ID>[/bold] to see full description.[/dim]")


# ──────────────────────────────────────────────────────────
# browse
# ──────────────────────────────────────────────────────────

@cli.command("browse")
@click.option("--unapplied", is_flag=True, help="Start with 'unapplied only' filter active")
@click.option("--board", "-b", default="all",
              type=click.Choice(["all", "linkedin", "indeed", "nodesk"], case_sensitive=False),
              help="Start with this board pre-selected")
def browse(unapplied: bool, board: str) -> None:
    """Interactive TUI for browsing and managing jobs.

    \b
    Keys:
      ↑ / ↓      navigate jobs
      Enter      expand / collapse description
      d          delete job (with confirmation)
      u          toggle 'unapplied only' filter
      r          refresh from database
      q / Esc    quit
    """
    from job_auto.tui.jobs_browser import JobsBrowser
    app = JobsBrowser(initial_unapplied=unapplied, initial_board=board)
    app.run()


# ──────────────────────────────────────────────────────────
# scan
# ──────────────────────────────────────────────────────────

@cli.command()
@click.argument("board", type=click.Choice(["linkedin", "indeed", "nodesk"], case_sensitive=False))
@click.option("--query", "-q", default="", help="Search query (title / keywords)")
@click.option("--limit", "-n", default=20, show_default=True, help="Max listings to fetch")
@click.option("--remote/--no-remote", default=True, show_default=True, help="Remote jobs only")
@click.option("--easy-apply", "easy_apply_only", is_flag=True, default=False,
              help="Only save Easy Apply jobs; silently drop the rest")
@click.option("--auth-flow", is_flag=True, default=False,
              help="Use authenticated Playwright browser (LinkedIn only). "
                   "Required for reliable Easy Apply server-side filtering.")
def scan(board: str, query: str, limit: int, remote: bool, easy_apply_only: bool, auth_flow: bool) -> None:
    """Scan a job board for new listings matching criteria.

    \b
    Examples:
      job-auto scan linkedin -q "senior python engineer"
      job-auto scan linkedin -q "backend engineer" --easy-apply
      job-auto scan linkedin -q "backend engineer" --auth-flow --easy-apply
    """
    from job_auto.ingestion.linkedin_playwright import LinkedInAuthError
    from job_auto.pipeline import Pipeline

    pipeline = Pipeline()
    effective_easy_apply = easy_apply_only or config.scan_easy_apply_only

    async def _run():
        try:
            jobs, skipped = await pipeline.scan(
                board=board, query=query, limit=limit, remote=remote,
                easy_apply_only=effective_easy_apply,
                auth_flow=auth_flow,
            )
        except LinkedInAuthError as exc:
            console.print(f"[bold red]LinkedIn auth error:[/bold red] {exc}")
            console.print(
                "Run [bold]job-auto apply <linkedin-url>[/bold] first to create a session, "
                "or check that [bold]LINKEDIN_EMAIL[/bold] and [bold]LINKEDIN_PASSWORD[/bold] "
                "are set in [bold].env[/bold]."
            )
            raise SystemExit(1)

        if skipped:
            console.print(f"[dim]{skipped} non-Easy-Apply listing(s) skipped.[/dim]")
        if not jobs:
            console.print("[yellow]No new listings found.[/yellow]")
            return

        table = Table(title=f"New {board.title()} Listings ({len(jobs)})", border_style="cyan")
        table.add_column("ID", style="dim")
        table.add_column("Company", style="bold")
        table.add_column("Title")
        table.add_column("Remote")
        table.add_column("Easy Apply")

        for job in jobs:
            table.add_row(
                job.id,
                job.company[:30],
                job.title[:50],
                "✓" if job.remote else "",
                "✓" if job.easy_apply_available else "",
            )
        console.print(table)

    asyncio.run(_run())


# ──────────────────────────────────────────────────────────
# review
# ──────────────────────────────────────────────────────────

@cli.command()
@click.option("--list", "list_only", is_flag=True, help="Just list pending, don't review")
def review(list_only: bool) -> None:
    """Interactively review applications pending human approval."""
    from job_auto.review.queue import list_pending, process_queue

    if list_only:
        list_pending()
    else:
        process_queue()


# ──────────────────────────────────────────────────────────
# status
# ──────────────────────────────────────────────────────────

@cli.command()
@click.option("--last", "-n", default=30, show_default=True, help="Show last N applications")
@click.option(
    "--status-filter", "-s",
    default=None,
    help="Filter by status (e.g. submitted, failed, review_pending)",
)
def status(last: int, status_filter: Optional[str]) -> None:
    """Show recent application history."""
    from job_auto.db import repository as repo
    from job_auto.db.session import get_session
    from job_auto.models.application import ApplicationStatus

    filt = ApplicationStatus(status_filter) if status_filter else None

    with get_session() as session:
        apps = repo.list_applications(session, status=filt, limit=last)
        jobs = {j.id: j for j in repo.list_jobs(session, limit=500)}

    if not apps:
        console.print("[yellow]No applications found.[/yellow]")
        return

    table = Table(title=f"Applications (last {last})", border_style="blue")
    table.add_column("App ID", style="dim")
    table.add_column("Company", style="bold")
    table.add_column("Title")
    table.add_column("Status")
    table.add_column("Failures", justify="right")
    table.add_column("Date")

    status_colors = {
        "submitted": "green",
        "failed": "red",
        "review_pending": "yellow",
        "review_rejected": "red",
        "tailoring": "blue",
        "queued": "cyan",
    }

    for app in apps:
        job = jobs.get(app.job_id)
        status_val = app.status.value
        color = status_colors.get(status_val, "white")
        table.add_row(
            app.id,
            (job.company[:25] if job else "?"),
            (job.title[:40] if job else "?"),
            f"[{color}]{status_val}[/{color}]",
            str(app.failure_count) if app.failure_count else "",
            app.created_at.strftime("%m-%d %H:%M"),
        )

    console.print(table)


# ──────────────────────────────────────────────────────────
# retry-failed
# ──────────────────────────────────────────────────────────

@cli.command("retry-failed")
@click.option("--auto", is_flag=True, help="Skip human review on retry")
def retry_failed(auto: bool) -> None:
    """Retry all failed applications that haven't exceeded max retries."""
    from job_auto.db import repository as repo
    from job_auto.db.session import get_session
    from job_auto.models.application import ApplicationStatus
    from job_auto.pipeline import Pipeline

    with get_session() as session:
        failed = repo.list_applications(session, status=ApplicationStatus.FAILED)

    retryable = [a for a in failed if a.needs_retry]

    if not retryable:
        console.print("[green]No retryable failures found.[/green]")
        return

    console.print(f"[bold]Retrying {len(retryable)} application(s)...[/bold]")
    pipeline = Pipeline(autonomous=auto)

    async def _run():
        for app in retryable:
            with get_session() as session:
                job = repo.get_job(session, app.job_id)
            if job:
                console.print(f"  Retrying {app.id} — {job.company} / {job.title}")
                try:
                    await pipeline.apply(str(job.url), dry_run=False)
                except Exception as e:
                    console.print(f"  [red]Error: {e}[/red]")

    asyncio.run(_run())


# ──────────────────────────────────────────────────────────
# submit-queued
# ──────────────────────────────────────────────────────────

@cli.command("submit-queued")
@click.option("--limit", "-n", default=10, show_default=True, help="Max applications to submit")
def submit_queued(limit: int) -> None:
    """Submit all applications that were approved but not yet submitted (QUEUED status).

    \b
    Use after running: job-auto apply <url> --dry-run
    """
    from job_auto.pipeline import Pipeline, PipelineError

    pipeline = Pipeline()

    async def _run():
        try:
            apps = await pipeline.submit_queued(limit=limit)
        except PipelineError as e:
            console.print(f"[bold red]Pipeline error:[/bold red] {e}")
            raise SystemExit(1)

        if not apps:
            console.print("[yellow]No queued applications found.[/yellow]")
            return

        table = Table(title=f"Results ({len(apps)} processed)", border_style="green")
        table.add_column("App ID", style="dim")
        table.add_column("Job ID", style="dim")
        table.add_column("Status")

        status_colors = {
            "submitted": "green",
            "failed": "red",
        }
        for app in apps:
            color = status_colors.get(app.status.value, "white")
            table.add_row(app.id, app.job_id, f"[{color}]{app.status.value}[/{color}]")

        console.print(table)

    asyncio.run(_run())


# ──────────────────────────────────────────────────────────
# mode
# ──────────────────────────────────────────────────────────

@cli.command()
@click.argument("mode_name", type=click.Choice(["auto", "review"], case_sensitive=False))
def mode(mode_name: str) -> None:
    """Toggle between autonomous and review mode (persists to .env)."""
    new_val = "true" if mode_name == "auto" else "false"
    env_path = config.data_dir.parent / ".env"

    if env_path.exists():
        text = env_path.read_text(encoding="utf-8")
        if "AUTONOMOUS_MODE=" in text:
            lines = [
                f"AUTONOMOUS_MODE={new_val}" if l.startswith("AUTONOMOUS_MODE=") else l
                for l in text.splitlines()
            ]
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            with open(env_path, "a") as f:
                f.write(f"\nAUTONOMOUS_MODE={new_val}\n")
    else:
        console.print("[yellow].env file not found; cannot persist mode.[/yellow]")

    label = "autonomous (no review)" if mode_name == "auto" else "review (human approval required)"
    console.print(f"[green]Mode set to:[/green] {label}")


# ──────────────────────────────────────────────────────────
# kb (knowledge base)
# ──────────────────────────────────────────────────────────

@cli.group()
def kb() -> None:
    """Knowledge base commands."""


@kb.command("show")
@click.option("--board", "-b", default=None, help="Show one board (linkedin|indeed|nodesk)")
def kb_show(board: Optional[str]) -> None:
    """Show knowledge base summary."""
    from job_auto.knowledge_base.store import kb_store

    summary = kb_store.summary()
    if board:
        summary = {board: summary.get(board, {})}

    table = Table(title="Knowledge Base", border_style="magenta")
    table.add_column("Board", style="bold")
    table.add_column("Version")
    table.add_column("Successes", justify="right", style="green")
    table.add_column("Failures", justify="right", style="red")
    table.add_column("Patches", justify="right")
    table.add_column("Notes", justify="right")
    table.add_column("Last Updated")

    for b, info in summary.items():
        table.add_row(
            b,
            str(info.get("version", 1)),
            str(info.get("success_count", 0)),
            str(info.get("failure_count", 0)),
            str(info.get("patches", 0)),
            str(info.get("notes", 0)),
            str(info.get("last_updated", ""))[:19],
        )
    console.print(table)


# ──────────────────────────────────────────────────────────
# gmail-auth
# ──────────────────────────────────────────────────────────

@cli.command("gmail-auth")
def gmail_auth() -> None:
    """One-time Gmail OAuth2 authorization for automatic security code fetching.

    \b
    Setup steps:
      1. Create a Google Cloud project and enable the Gmail API.
      2. Create OAuth2 Desktop credentials and download credentials.json.
      3. Place it at storage/gmail_credentials.json.
      4. Run this command — a browser opens for consent.
         Token is saved to storage/gmail_token.json automatically.
    """
    from job_auto.utils.gmail import authenticate

    creds_path = config.gmail_credentials_path
    token_path = config.gmail_token_path

    if not creds_path.exists():
        console.print(
            f"[red]Credentials file not found:[/red] {creds_path}\n"
            "Download OAuth2 Desktop credentials from Google Cloud Console "
            "and save them there."
        )
        raise SystemExit(1)

    console.print("[bold]Before authorizing, confirm these steps are done:[/bold]")
    console.print("  1. Gmail API is enabled for your Google Cloud project")
    console.print("  2. OAuth consent screen → Test users: your Gmail address is listed")
    console.print("  3. credentials.json was downloaded from OAuth 2.0 Client IDs (Desktop type)")
    console.print()
    console.print("Opening browser for authorization...")

    authenticate(creds_path, token_path)
    console.print(f"[green]Gmail authorized.[/green] Token saved to {token_path}")


if __name__ == "__main__":
    cli()
