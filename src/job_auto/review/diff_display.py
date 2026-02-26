"""Rich-powered diff display: base resume vs tailored resume."""

from __future__ import annotations

import difflib
from typing import Optional

from rich.columns import Columns
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from job_auto.models.application import ApplicationRecord, ApplicationStatus
from job_auto.models.job_posting import JobPosting
from job_auto.utils.logging import get_logger

logger = get_logger(__name__)

console = Console()


def display_job_summary(job: JobPosting) -> None:
    """Print a summary panel for the job posting."""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Field", style="bold cyan", min_width=14)
    table.add_column("Value")

    table.add_row("Company", escape(job.company))
    table.add_row("Title", escape(job.title))
    table.add_row("Board", job.board.value.upper())
    table.add_row("URL", f"[link={job.url_str}]{escape(job.url_str[:70])}[/link]")
    if job.salary_range:
        table.add_row("Salary", job.salary_range)
    table.add_row("Remote", "Yes" if job.remote else "No")
    table.add_row("Easy Apply", "Yes" if job.easy_apply_available else "No")
    if job.tech_stack:
        table.add_row("Tech Stack", escape(", ".join(job.tech_stack[:10])))

    console.print(Panel(table, title=f"[bold]{escape(job.title)}[/bold]", border_style="cyan"))


def display_resume_diff(base_text: str, tailored_text: str) -> None:
    """Show a side-by-side or unified diff of base vs tailored resume."""
    console.print(Rule("[bold]Resume Changes[/bold]", style="yellow"))

    # Generate unified diff
    base_lines = base_text.splitlines(keepends=True)
    tailored_lines = tailored_text.splitlines(keepends=True)
    diff = list(difflib.unified_diff(
        base_lines, tailored_lines,
        fromfile="base_resume.md",
        tofile="tailored_resume.md",
        lineterm="",
    ))

    if not diff:
        console.print("[green]No changes detected.[/green]")
        return

    diff_text = "".join(diff)
    syntax = Syntax(diff_text, "diff", theme="monokai", line_numbers=False, word_wrap=True)
    console.print(Panel(syntax, title="Unified Diff (base → tailored)", border_style="yellow"))

    # Summary stats
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    console.print(f"  [green]+{added} lines added[/green]  [red]-{removed} lines removed[/red]\n")


def display_cover_letter(cover_letter_text: str) -> None:
    """Display the generated cover letter."""
    console.print(Rule("[bold]Cover Letter[/bold]", style="green"))
    console.print(Panel(
        escape(cover_letter_text),
        title="Generated Cover Letter",
        border_style="green",
    ))


def review_application(
    job: JobPosting,
    app: ApplicationRecord,
    base_resume: str,
) -> tuple[ApplicationStatus, Optional[str]]:
    """
    Interactive review flow.

    Returns (new_status, optional_edit_notes).
    new_status is QUEUED (approved) or REVIEW_REJECTED (rejected).
    """
    console.print()
    display_job_summary(job)
    console.print()

    if app.tailored_resume_text:
        display_resume_diff(base_resume, app.tailored_resume_text)

    if app.cover_letter_text:
        display_cover_letter(app.cover_letter_text)

    console.print(Rule("[bold]Review Decision[/bold]", style="blue"))
    console.print("[bold]Options:[/bold]")
    console.print("  [green]a[/green] — Approve and queue for submission")
    console.print("  [red]r[/red] — Reject (skip this application)")
    console.print("  [yellow]e[/yellow] — Approve with edit notes (for manual tweaks)")
    console.print("  [blue]s[/blue] — Skip for now (review later)")

    while True:
        choice = Prompt.ask("Your choice", choices=["a", "r", "e", "s"], default="a")
        if choice == "a":
            console.print("[green]✓ Approved for submission.[/green]")
            return ApplicationStatus.QUEUED, None
        elif choice == "r":
            reason = Prompt.ask("Rejection reason (optional)", default="")
            console.print("[red]✗ Application rejected.[/red]")
            return ApplicationStatus.REVIEW_REJECTED, reason or None
        elif choice == "e":
            notes = Prompt.ask("Edit notes (will be logged for reference)")
            console.print("[yellow]✓ Approved with notes.[/yellow]")
            return ApplicationStatus.QUEUED, notes
        elif choice == "s":
            console.print("[blue]→ Skipped (status unchanged).[/blue]")
            return ApplicationStatus.REVIEW_PENDING, None
