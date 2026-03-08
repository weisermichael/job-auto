"""Rich-based interactive review of pattern-matched answers awaiting verification."""

from __future__ import annotations

import re

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from job_auto.knowledge_base.store import kb_store

console = Console()


def verify_answers_for_board(board: str) -> int:
    """Interactively confirm or override pattern-matched answers for *board*.

    Each answer was applied to a form automatically but has not yet been
    reviewed by the user.  Confirmed answers graduate to qa_cache; overrides
    are saved with the user-supplied value.

    Returns the number of answers confirmed/overridden.
    """
    pending = kb_store.get_qa_pending_verification(board)
    if not pending:
        return 0

    total = len(pending)
    console.print(Panel(
        f"[bold]{total} answer(s)[/bold] were applied automatically and need your review.\n"
        f"[dim]Board: {board}[/dim]",
        title="Verify Pattern-Matched Answers",
        border_style="cyan",
    ))
    console.print(
        "Press [bold]Enter[/bold] to accept the suggested answer, "
        "or type a replacement.\n"
    )

    confirmed = 0
    items = list(pending.items())

    for i, (key, entry) in enumerate(items, 1):
        label = entry.get("label", key)
        suggested = entry.get("answer", "")
        ftype = entry.get("type", "text")
        options: list[str] = entry.get("options", [])

        console.print(f"[bold][{i}/{total}][/bold] [cyan]{escape(label)}[/cyan]")
        console.print(f"  [dim]Type: {ftype}  |  Suggested: {escape(suggested)}[/dim]")

        if ftype in ("select", "radio") and options:
            # Show numbered list; mark suggested
            for j, opt in enumerate(options, 1):
                marker = " [green]<-- suggested[/green]" if opt == suggested else ""
                console.print(f"    {j}. {escape(opt)}{marker}")
            default_idx = next(
                (str(j) for j, opt in enumerate(options, 1) if opt == suggested),
                "1",
            )
            while True:
                raw = Prompt.ask(
                    f"  Enter number (1\u2013{len(options)}) or exact text",
                    default=default_idx,
                )
                if raw.isdigit():
                    idx = int(raw) - 1
                    if 0 <= idx < len(options):
                        answer = options[idx]
                        break
                    console.print(f"  [red]Enter a number between 1 and {len(options)}.[/red]")
                elif raw in options:
                    answer = raw
                    break
                else:
                    console.print("  [red]Invalid choice.[/red]")

        elif ftype == "checkbox":
            default_bool = suggested.lower() in ("yes", "true", "1")
            answer = "Yes" if Confirm.ask(f"  {escape(label)}", default=default_bool) else "No"

        else:
            answer = Prompt.ask("  Answer", default=suggested)

        kb_store.resolve_qa_pending_verification(board, key, answer)
        confirmed += 1
        console.print("  [green]\u2713 Saved[/green]\n")

    return confirmed
