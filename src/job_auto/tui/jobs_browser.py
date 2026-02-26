"""Interactive TUI for browsing and managing jobs in the database."""

from __future__ import annotations

from typing import Optional

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.screen import ModalScreen
from rich.text import Text
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Label,
    RichLog,
    Select,
    Static,
)

from job_auto.db import repository as repo
from job_auto.db.session import get_session
from job_auto.models.job_posting import JobPosting


# ── Delete confirmation modal ────────────────────────────────────────────────

class ConfirmDelete(ModalScreen[bool]):
    """Modal dialog: confirm before deleting a job."""

    CSS = """
    ConfirmDelete {
        align: center middle;
    }
    ConfirmDelete > Vertical {
        background: $surface;
        border: thick $error;
        padding: 1 2;
        width: 60;
        height: auto;
    }
    ConfirmDelete Label {
        width: 1fr;
        content-align: center middle;
        padding-bottom: 1;
    }
    ConfirmDelete Horizontal {
        align: center middle;
        height: auto;
    }
    ConfirmDelete Button {
        margin: 0 2;
    }
    """

    def __init__(self, job: JobPosting) -> None:
        super().__init__()
        self._job = job

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(
                f"Delete [bold]{self._job.title}[/bold]\n"
                f"at [bold]{self._job.company}[/bold]?\n\n"
                "This cannot be undone.",
                markup=True,
            )
            with Horizontal():
                yield Button("Delete", variant="error", id="confirm")
                yield Button("Cancel", variant="default", id="cancel")

    @on(Button.Pressed, "#confirm")
    def do_delete(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def do_cancel(self) -> None:
        self.dismiss(False)


# ── Main app ─────────────────────────────────────────────────────────────────

class JobsBrowser(App):
    """Interactive job browser TUI."""

    TITLE = "job-auto  ·  Job Browser"
    CSS = """
    Screen {
        layout: vertical;
    }

    /* ── toolbar ── */
    #toolbar {
        height: 3;
        background: $panel;
        padding: 0 1;
        layout: horizontal;
        align: left middle;
    }
    #toolbar Label {
        margin-right: 2;
        color: $text-muted;
    }
    #toolbar Select {
        width: 20;
        margin-right: 2;
    }
    #job-count {
        color: $text-muted;
        margin-left: 1;
    }

    /* ── job table ── */
    #table-container {
        height: 1fr;
        border: solid $panel-lighten-2;
    }
    DataTable {
        height: 1fr;
    }
    DataTable > .datatable--header {
        background: $panel;
        color: $text;
        text-style: bold;
    }
    DataTable > .datatable--cursor {
        background: $accent;
        color: $text;
    }

    /* ── description panel ── */
    #detail-panel {
        height: 14;
        border-top: solid $accent;
    }
    #detail-header {
        background: $panel;
        color: $text-muted;
        padding: 0 1;
        height: 1;
    }
    #detail-scroll {
        height: 1fr;
        padding: 0 1;
    }
    #detail-body {
        color: $text;
    }

    /* ── status bar ── */
    #status-bar {
        height: 1;
        background: $panel-darken-1;
        padding: 0 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("d",        "delete_job",    "Delete",         show=True),
        Binding("r",        "refresh",       "Refresh",        show=True),
        Binding("u",        "toggle_filter", "Unapplied only", show=True),
        Binding("escape",   "collapse",      "Collapse",       show=True),
        Binding("q",        "quit",          "Quit",           show=True),
    ]

    _jobs: list[JobPosting] = []

    def __init__(self, initial_unapplied: bool = False, initial_board: str = "all") -> None:
        super().__init__()
        self._show_unapplied_only = initial_unapplied
        self._board_filter = initial_board

    # ── layout ──────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Horizontal(id="toolbar"):
            yield Label("Board:")
            yield Select(
                options=[
                    ("All boards", "all"),
                    ("LinkedIn",   "linkedin"),
                    ("Indeed",     "indeed"),
                    ("Nodesk",     "nodesk"),
                ],
                value=self._board_filter,
                id="board-select",
                allow_blank=False,
            )
            yield Label("  ")
            yield Button(
                "Unapplied only",
                id="unapplied-btn",
                variant="success" if self._show_unapplied_only else "default",
            )
            yield Static("", id="job-count")

        with Vertical(id="table-container"):
            yield DataTable(id="job-table", cursor_type="row", zebra_stripes=True)

        with Vertical(id="detail-panel"):
            yield Static("", id="detail-header")
            with ScrollableContainer(id="detail-scroll"):
                yield RichLog(id="detail-body", markup=True, highlight=False, wrap=True)

        yield Static("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#job-table", DataTable)
        table.add_columns(
            "ID", "Board", "Company", "Title", "Remote", "Easy Apply", "Salary", "Found",
        )
        self.query_one("#detail-panel").display = False
        self._load_jobs()
        table.focus()

    # ── data loading ─────────────────────────────────────────────────────────

    def _load_jobs(self) -> None:
        with get_session() as session:
            if self._show_unapplied_only:
                jobs = repo.list_unapplied_jobs(session, limit=500)
            else:
                jobs = repo.list_jobs(session, limit=500)

        if self._board_filter != "all":
            jobs = [j for j in jobs if j.board.value == self._board_filter]

        self._jobs = jobs
        self._populate_table()

    def _populate_table(self) -> None:
        table = self.query_one("#job-table", DataTable)
        table.clear()

        for job in self._jobs:
            table.add_row(
                job.id[:8] + "…",
                job.board.value,
                job.company[:28],
                job.title[:42],
                "✓" if job.remote else "",
                "✓" if job.easy_apply_available else "",
                job.salary_range or "",
                job.date_found.strftime("%m-%d %H:%M"),
                key=job.id,
            )

        count = len(self._jobs)
        noun = "job" if count == 1 else "jobs"
        suffix = "  [unapplied only]" if self._show_unapplied_only else ""
        self.query_one("#job-count", Static).update(f"[dim]{count} {noun}{suffix}[/dim]")
        self._set_status(f"{count} {noun} loaded  ·  Enter to expand  ·  d to delete")

    # ── detail panel ─────────────────────────────────────────────────────────

    def _job_at_cursor(self) -> Optional[JobPosting]:
        table = self.query_one("#job-table", DataTable)
        if table.row_count == 0:
            return None
        idx = table.cursor_row
        if idx < 0 or idx >= len(self._jobs):
            return None
        return self._jobs[idx]

    def _show_detail(self, job: JobPosting) -> None:
        panel = self.query_one("#detail-panel")
        panel.display = True

        header = Text(no_wrap=True, overflow="ellipsis")
        header.append(f" {job.title}", style="bold")
        header.append(f"  ·  {job.company}  ·  [{job.board.value.upper()}]")
        if job.salary_range:
            header.append(f"  ·  {job.salary_range}")
        header.append("  ·  ")
        header.append("link", style=f"link {job.url_str}")
        self.query_one("#detail-header", Static).update(header)

        body = self.query_one("#detail-body", RichLog)
        body.clear()
        body.write(job.description.strip() or "[dim](no description available)[/dim]")

    def _hide_detail(self) -> None:
        self.query_one("#detail-panel").display = False

    # ── actions ──────────────────────────────────────────────────────────────

    def action_collapse(self) -> None:
        """Escape: collapse the detail panel if open, otherwise do nothing."""
        if self.query_one("#detail-panel").display:
            self._hide_detail()

    def action_delete_job(self) -> None:
        job = self._job_at_cursor()
        if job:
            self._run_delete(job)

    @work
    async def _run_delete(self, job: JobPosting) -> None:
        confirmed = await self.push_screen_wait(ConfirmDelete(job))
        if not confirmed:
            return

        with get_session() as session:
            app_record = repo.get_application_by_job(session, job.id)
            if app_record:
                from job_auto.db.models import ApplicationRecordORM
                orm_app = session.get(ApplicationRecordORM, app_record.id)
                if orm_app:
                    session.delete(orm_app)
            from job_auto.db.models import JobPostingORM
            orm_job = session.get(JobPostingORM, job.id)
            if orm_job:
                session.delete(orm_job)

        self._hide_detail()
        self._load_jobs()
        self._set_status(f"Deleted: {job.title} at {job.company}")

    def action_refresh(self) -> None:
        self._load_jobs()
        self._set_status("Refreshed")

    def action_toggle_filter(self) -> None:
        self._show_unapplied_only = not self._show_unapplied_only
        btn = self.query_one("#unapplied-btn", Button)
        btn.variant = "success" if self._show_unapplied_only else "default"
        self._hide_detail()
        self._load_jobs()

    # ── event handlers ────────────────────────────────────────────────────────

    @on(DataTable.RowSelected)
    def row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter on a row: toggle detail panel."""
        panel = self.query_one("#detail-panel")
        if panel.display:
            # Already open on this row → collapse
            self._hide_detail()
        else:
            job = self._job_at_cursor()
            if job:
                self._show_detail(job)

    @on(DataTable.RowHighlighted)
    def row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Arrow-key navigation: update detail panel if it's already open."""
        if self.query_one("#detail-panel").display:
            job = self._job_at_cursor()
            if job:
                self._show_detail(job)

    @on(Select.Changed, "#board-select")
    def board_changed(self, event: Select.Changed) -> None:
        self._board_filter = str(event.value)
        self._hide_detail()
        self._load_jobs()

    @on(Button.Pressed, "#unapplied-btn")
    def unapplied_btn_pressed(self) -> None:
        self.action_toggle_filter()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#status-bar", Static).update(f" {msg}")
        except NoMatches:
            pass
