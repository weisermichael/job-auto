"""Review queue: list and process pending applications."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from job_auto.ai.tailoring import load_base_resume
from job_auto.db import repository as repo
from job_auto.db.session import get_session
from job_auto.models.application import ApplicationStatus
from job_auto.review.diff_display import console, review_application
from job_auto.utils.logging import get_logger

logger = get_logger(__name__)


def list_pending() -> None:
    """Print all applications pending review."""
    with get_session() as session:
        apps = repo.list_applications(session, status=ApplicationStatus.REVIEW_PENDING)

    if not apps:
        console.print("[green]No applications pending review.[/green]")
        return

    table = Table(title=f"Review Queue ({len(apps)} pending)", border_style="blue")
    table.add_column("App ID", style="dim")
    table.add_column("Company", style="bold")
    table.add_column("Title")
    table.add_column("Mode")
    table.add_column("Created")

    for app in apps:
        with get_session() as session:
            job = repo.get_job(session, app.job_id)
        if job:
            table.add_row(
                app.id,
                job.company,
                job.title,
                "auto" if app.autonomous_mode else "review",
                app.created_at.strftime("%Y-%m-%d %H:%M"),
            )

    console.print(table)


def process_queue() -> None:
    """Interactively review all pending applications."""
    with get_session() as session:
        apps = repo.list_applications(session, status=ApplicationStatus.REVIEW_PENDING)

    if not apps:
        console.print("[green]No applications pending review.[/green]")
        return

    console.print(f"[bold]{len(apps)} application(s) pending review.[/bold]\n")
    base_resume = load_base_resume()

    for i, app in enumerate(apps, 1):
        console.print(f"\n[bold]Application {i}/{len(apps)}[/bold]")

        with get_session() as session:
            job = repo.get_job(session, app.job_id)

        if not job:
            logger.warning("review_job_not_found", app_id=app.id, job_id=app.job_id)
            continue

        new_status, notes = review_application(job, app, base_resume)

        if new_status != ApplicationStatus.REVIEW_PENDING:
            app.status = new_status
            if notes:
                app.last_correction = notes
            with get_session() as session:
                repo.update_application(session, app)
            logger.info("review_decision", app_id=app.id, status=new_status.value)

    console.print("\n[bold green]Review session complete.[/bold green]")
