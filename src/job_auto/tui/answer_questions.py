"""Rich-based interactive Q&A session for NEEDS_ANSWERS applications."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from job_auto.knowledge_base.store import kb_store

if TYPE_CHECKING:
    from job_auto.models.application import ApplicationRecord
    from job_auto.models.job_posting import JobPosting

console = Console()


def _prompt_field(q: dict) -> str:
    """Prompt for one question's answer based on field type."""
    label = q.get("label", "?")
    ftype = q.get("type", "text")
    options = q.get("options", [])

    console.print(f"[bold]Question:[/bold] {escape(label)}")
    console.print(f"[dim]Type: {ftype}[/dim]")

    if ftype in ("select", "radio") and options:
        for i, opt in enumerate(options, 1):
            console.print(f"  {i}. {escape(str(opt))}")
        while True:
            raw = Prompt.ask(f"  Enter number (1\u2013{len(options)}) or exact text")
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(options):
                    return options[idx]
                console.print(f"  [red]Please enter a number between 1 and {len(options)}.[/red]")
            elif raw in options:
                return raw
            else:
                console.print("  [red]Invalid choice. Enter a number or exact option text.[/red]")

    elif ftype == "checkbox":
        return "Yes" if Confirm.ask(f"  {escape(label)}") else "No"

    elif ftype == "textarea":
        console.print("  [dim](Enter multiple lines; press Enter on a blank line to finish)[/dim]")
        lines = []
        while True:
            line = Prompt.ask("  ", default="")
            if line == "":
                break
            lines.append(line)
        return "\n".join(lines)

    else:
        return Prompt.ask("  Answer")


def answer_questions_for_app(
    app: "ApplicationRecord",
    job: "JobPosting",
    board: str = "linkedin",
) -> dict[str, str]:
    """Run an interactive Q&A session for one NEEDS_ANSWERS application.

    Saves each answer to qa_cache and removes it from pending_questions
    immediately after the user answers it, so Ctrl-C leaves a clean state.

    Returns a dict of {cache_key: answer}.
    """
    job_url = str(job.url)
    questions = kb_store.get_pending_questions(board, job_url)
    if not questions:
        console.print("[yellow]No pending questions found for this application.[/yellow]")
        return {}

    console.print(Panel(
        f"[bold]{escape(job.title)}[/bold] at [bold]{escape(job.company)}[/bold]\n"
        f"[dim]{escape(job_url)}[/dim]",
        title="Answer Required Questions",
        border_style="cyan",
    ))
    console.print(f"{len(questions)} required question(s) could not be answered automatically.\n")

    answered: dict[str, str] = {}
    total = len(questions)

    for i, q in enumerate(questions, 1):
        label = q.get("label", "")
        console.print(f"[bold]{i} of {total}[/bold]")
        answer = _prompt_field(q)
        cache_key = re.sub(r"\s+", " ", label.lower()).strip()
        kb_store.set_qa_answer(board, cache_key, answer)
        kb_store.resolve_pending_question(board, job_url, label)
        answered[cache_key] = answer
        console.print("  [green]\u2713 Saved[/green]\n")

    console.print(f"[green]All {len(answered)} answer(s) saved to the knowledge base.[/green]\n")
    return answered
