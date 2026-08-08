"""Watch daemon and schedule management commands for scheduled research.

Provides:

* ``cmd_watch`` — long-running daemon that starts the
  :class:`~src.scheduled_research.executor.ScheduledResearchExecutor`,
  dispatches due jobs through ``AgentLoop``, and handles SIGTERM for
  graceful shutdown.
* ``cmd_schedule_add`` / ``cmd_schedule_list`` / ``cmd_schedule_delete`` /
  ``cmd_schedule_pause`` / ``cmd_schedule_resume`` — CRUD management for
  scheduled research jobs.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import smtplib
import sys
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import rich
from rich.console import Console as RichConsole
from rich.table import Table as RichTable

from cli.theme import get_console
from src.config.accessor import get_env_config
from src.scheduled_research.executor import (
    ScheduledResearchExecutor,
    scheduler_enabled_from_env,
)
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore

logger = logging.getLogger(__name__)

EXIT_SUCCESS = 0
EXIT_RUN_FAILED = 1
EXIT_USAGE_ERROR = 2

console: RichConsole = get_console()


# ---------------------------------------------------------------------------
# Dispatch bridge — feeds scheduled prompts to AgentLoop
# ---------------------------------------------------------------------------


async def _dispatch_job(job: ScheduledResearchJob) -> None:
    """Feed a scheduled research job's prompt to the AgentLoop.

    This is the bridge between the polling executor and the research engine.
    Each job dispatch creates a fresh AgentLoop instance, runs the prompt
    through the full ReAct cycle, and logs the result.

    Args:
        job: The due scheduled research job.
    """
    from cli._legacy import _run_agent

    prompt = job.prompt
    if not prompt or not prompt.strip():
        logger.warning("scheduled research job %s has empty prompt; skipping", job.id)
        return

    logger.info("dispatching scheduled research job %s: %s", job.id, prompt[:120])
    now = time.time()
    try:
        result = await asyncio.to_thread(
            _run_agent,
            prompt,
            history=None,
            max_iter=30,
            no_rich=True,
            stream_output=False,
        )
        elapsed = time.time() - now
        content = (result.get("content") or "").strip()
        logger.info(
            "scheduled research job %s completed in %.1fs; status=%s; output=%d chars",
            job.id,
            elapsed,
            result.get("status", "unknown"),
            len(content),
        )
        if content:
            logger.debug("job %s output preview: %s", job.id, content[:300])
    except Exception:
        logger.error("scheduled research job %s failed", job.id, exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Notification — email delivery after job completion
# ---------------------------------------------------------------------------


def _read_env_val(key: str) -> str | None:
    """Read an environment variable, returning None when unset or empty."""
    val = os.getenv(key)
    return val.strip() if val else None


async def _notify_job_result(job: ScheduledResearchJob, status: str, summary: str) -> None:
    """Notify about a completed or failed scheduled job.

    Tries email (SMTP) first; falls back to console print. Configuration is
    read from the standard environment variables:

    * ``SCHEDULE_NOTIFY_EMAIL_TO`` — recipient address (required for email)
    * ``SCHEDULE_NOTIFY_EMAIL_FROM`` — sender address
    * ``SCHEDULE_NOTIFY_SMTP_HOST`` — SMTP server (default: localhost)
    * ``SCHEDULE_NOTIFY_SMTP_PORT`` — SMTP port (default: 25)
    * ``SCHEDULE_NOTIFY_SMTP_USER`` / ``SCHEDULE_NOTIFY_SMTP_PASS`` — auth

    Falls back to ``EMAIL_TO`` / ``EMAIL_FROM`` / ``SMTP_HOST`` / etc. from
    the channels config when schedule-specific vars are not set.

    Args:
        job: The job that completed.
        status: ``"completed"`` or ``"failed"``.
        summary: Human-readable result string.
    """
    to_addr = _read_env_val("SCHEDULE_NOTIFY_EMAIL_TO") or _read_env_val("EMAIL_TO")
    if not to_addr:
        # No email config — print to console as fallback.
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        status_icon = "+" if status == "completed" else "x"
        console.print(
            f"[dim]{ts}[/dim] [bold]Scheduled job[/bold] "
            f"[cyan]{job.id}[/cyan] "
            f"[{'green' if status == 'completed' else 'red'}]{status_icon} "
            f"{status.upper()}[/{'green' if status == 'completed' else 'red'}] "
            f"[dim]{job.prompt[:80]}[/dim]"
        )
        return

    from_addr = _read_env_val("SCHEDULE_NOTIFY_EMAIL_FROM") or _read_env_val("EMAIL_FROM") or "vmx@localhost"
    smtp_host = _read_env_val("SCHEDULE_NOTIFY_SMTP_HOST") or _read_env_val("SMTP_HOST") or "localhost"
    smtp_port_str = _read_env_val("SCHEDULE_NOTIFY_SMTP_PORT") or _read_env_val("SMTP_PORT") or "25"
    smtp_user = _read_env_val("SCHEDULE_NOTIFY_SMTP_USER") or _read_env_val("SMTP_USER")
    smtp_pass = _read_env_val("SCHEDULE_NOTIFY_SMTP_PASS") or _read_env_val("SMTP_PASS")

    try:
        smtp_port = int(smtp_port_str)
    except ValueError:
        smtp_port = 25

    subject = f"[VMaxxing] Scheduled job {job.id} {status.upper()}"
    last_run = (
        datetime.fromtimestamp(job.last_run_at / 1000, timezone.utc).isoformat()
        if job.last_run_at
        else "N/A"
    )
    body = f"""\
Scheduled Research Job: {job.id}
Status: {status.upper()}
Prompt: {job.prompt}
Schedule: {job.schedule}
Last Run: {last_run}
Next Run: {datetime.fromtimestamp(job.next_run_at / 1000, timezone.utc).isoformat()}

{summary}
"""

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr

    try:
        loop = asyncio.get_running_loop()

        def _send() -> None:
            if smtp_port == 465:
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10)
            else:
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
            try:
                if smtp_user and smtp_pass:
                    server.login(smtp_user, smtp_pass)
                server.sendmail(from_addr, [to_addr], msg.as_string())
            finally:
                server.quit()

        await loop.run_in_executor(None, _send)
        logger.info("notification email sent to %s for job %s", to_addr, job.id)
    except Exception:
        logger.error("failed to send notification email for job %s", job.id, exc_info=True)
        # Fall back to console.
        console.print(
            f"[yellow]Scheduled job [cyan]{job.id}[/cyan] {status.upper()}; "
            f"email notification failed (see log)[/yellow]"
        )


# ---------------------------------------------------------------------------
# Watch daemon
# ---------------------------------------------------------------------------


def cmd_watch(
    *,
    tick_interval_ms: int = 60_000,
    store_path: Optional[Path] = None,
) -> int:
    """Start the scheduled-research daemon.

    Loads persisted jobs, wires the AgentLoop dispatch bridge, and runs the
    executor in a long-lived asyncio loop. Handles SIGTERM/SIGINT for graceful
    shutdown.

    The daemon checks ``VIBE_TRADING_ENABLE_SCHEDULER`` — when disabled (the
    default), it prints a warning and exits with code 0. Enable it with::

        export VIBE_TRADING_ENABLE_SCHEDULER=1
        vmx watch

    Args:
        tick_interval_ms: Poll interval in milliseconds (default: 60s).
        store_path: Optional explicit store path.

    Returns:
        Process exit code.
    """
    if not scheduler_enabled_from_env():
        console.print(
            "[yellow]Scheduled research is disabled.[/yellow] "
            "Set [bold]VIBE_TRADING_ENABLE_SCHEDULER=1[/bold] in your environment "
            "or [bold]~/.vmx/.env[/bold] to enable it."
        )
        return EXIT_SUCCESS

    console.print("[bold cyan]Starting scheduled-research daemon[/bold cyan]")
    console.print(f"  Poll interval: {tick_interval_ms // 1000}s")
    console.print(f"  Store: {store_path or ScheduledResearchJobStore().path}")

    store = ScheduledResearchJobStore(path=store_path)

    jobs = store.load()
    console.print(f"  Loaded {len(jobs)} persisted job(s)")

    executor = ScheduledResearchExecutor(
        store=store,
        dispatch=_dispatch_job,
        tick_interval_ms=tick_interval_ms,
        enabled=True,
        notify=_notify_job_result,
    )

    async def _run_daemon() -> None:
        executor.start()
        console.print("[green]Daemon running. Press Ctrl+C to stop.[/green]")

        # Keep the loop alive until a signal arrives.
        stop_event = asyncio.Event()

        def _on_signal(signum: int, frame: object) -> None:
            sig_name = signal.Signals(signum).name
            console.print(f"\n[dim]Received {sig_name}, shutting down...[/dim]")
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, lambda s=sig: _on_signal(s, None))
            except NotImplementedError:
                # Windows doesn't support add_signal_handler.
                pass

        try:
            await stop_event.wait()
        finally:
            console.print("[dim]Stopping executor...[/dim]")
            await executor.stop()
            console.print("[dim]Daemon stopped.[/dim]")

    try:
        asyncio.run(_run_daemon())
    except KeyboardInterrupt:
        console.print("\n[dim]Daemon interrupted. Goodbye.[/dim]")

    return EXIT_SUCCESS


# ---------------------------------------------------------------------------
# Schedule management commands
# ---------------------------------------------------------------------------


def _format_job_row(job: ScheduledResearchJob) -> dict:
    """Format a job for table display."""
    status_style = {
        "pending": "yellow",
        "running": "cyan",
        "completed": "green",
        "failed": "red",
        "cancelled": "dim",
        "paused": "magenta",
    }.get(job.status.value, "white")

    next_run = ""
    if job.next_run_at:
        dt = datetime.fromtimestamp(job.next_run_at / 1000, timezone.utc)
        next_run = dt.strftime("%Y-%m-%d %H:%M")

    last_run = ""
    if job.last_run_at:
        dt = datetime.fromtimestamp(job.last_run_at / 1000, timezone.utc)
        last_run = dt.strftime("%Y-%m-%d %H:%M")

    return {
        "id": job.id,
        "prompt": job.prompt[:60] + ("..." if len(job.prompt) > 60 else ""),
        "schedule": job.schedule,
        "status": f"[{status_style}]{job.status.value}[/{status_style}]",
        "next_run": next_run,
        "last_run": last_run,
    }


def cmd_schedule_list() -> int:
    """List all scheduled research jobs."""
    store = ScheduledResearchJobStore()
    try:
        jobs = store.list_jobs(limit=100)
    except Exception as exc:
        console.print(f"[red]Failed to load jobs: {exc}[/red]")
        return EXIT_RUN_FAILED

    if not jobs:
        console.print("[dim]No scheduled research jobs.[/dim]")
        console.print(
            "Add one with: [bold]vmx schedule add <id> <prompt> <schedule>[/bold]"
        )
        return EXIT_SUCCESS

    table = RichTable(title="Scheduled Research Jobs", show_header=True, header_style="bold")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Prompt", style="white")
    table.add_column("Schedule", style="dim")
    table.add_column("Status")
    table.add_column("Next Run", style="dim")
    table.add_column("Last Run", style="dim")

    for job in jobs:
        row = _format_job_row(job)
        table.add_row(
            row["id"],
            row["prompt"],
            row["schedule"],
            row["status"],
            row["next_run"],
            row["last_run"],
        )

    console.print(table)
    console.print(
        f"\n[dim]{len(jobs)} job(s) total. "
        "Manage with: schedule add | delete | pause | resume[/dim]"
    )
    return EXIT_SUCCESS


def cmd_schedule_add(job_id: str, prompt: str, schedule: str) -> int:
    """Add a new scheduled research job.

    Args:
        job_id: Unique job identifier (slug or UUID).
        prompt: Research prompt to execute.
        schedule: Interval-ms string (e.g. ``"3600000"`` for hourly) or
            5-field cron expression (e.g. ``"0 9 * * 1"`` for Monday 9am UTC).
    """
    from src.scheduled_research.models import validate_schedule

    try:
        validate_schedule(schedule)
    except ValueError as exc:
        console.print(f"[red]Invalid schedule: {exc}[/red]")
        console.print("[dim]Schedule must be a positive integer (ms) or a 5-field cron expression.[/dim]")
        console.print("[dim]Examples: '3600000' (hourly), '0 9 * * 1' (Monday 9am UTC), '*/30 * * * *' (every 30 min)[/dim]")
        return EXIT_USAGE_ERROR

    now_ms = int(time.time() * 1000)
    job = ScheduledResearchJob(
        id=job_id,
        prompt=prompt,
        schedule=schedule,
        next_run_at=now_ms,
        status=JobStatus.PENDING,
        created_at=now_ms,
    )

    store = ScheduledResearchJobStore()
    existing = store.get(job_id)
    if existing is not None:
        console.print(
            f"[yellow]Job [cyan]{job_id}[/cyan] already exists.[/yellow] "
            "Use [bold]schedule delete[/bold] first to replace it."
        )
        return EXIT_USAGE_ERROR

    try:
        store.upsert(job)
    except Exception as exc:
        console.print(f"[red]Failed to save job: {exc}[/red]")
        return EXIT_RUN_FAILED

    console.print(f"[green]Job [bold cyan]{job_id}[/bold cyan] added.[/green]")
    console.print(f"  Prompt:   {prompt[:100]}")
    console.print(f"  Schedule: {schedule}")
    console.print(f"  Status:   pending (runs immediately on next daemon tick)")
    return EXIT_SUCCESS


def cmd_schedule_delete(job_id: str) -> int:
    """Delete a scheduled research job.

    Args:
        job_id: Job identifier to remove.
    """
    store = ScheduledResearchJobStore()
    try:
        deleted = store.delete(job_id)
    except Exception as exc:
        console.print(f"[red]Failed to delete job: {exc}[/red]")
        return EXIT_RUN_FAILED

    if deleted:
        console.print(f"[green]Job [bold cyan]{job_id}[/bold cyan] deleted.[/green]")
        return EXIT_SUCCESS
    else:
        console.print(f"[yellow]Job [cyan]{job_id}[/cyan] not found.[/yellow]")
        return EXIT_USAGE_ERROR


def cmd_schedule_pause(job_id: str) -> int:
    """Pause a scheduled research job.

    Paused jobs are excluded from dispatch until resumed. Their
    ``next_run_at`` is preserved so they fire immediately on resume if
    overdue.

    Args:
        job_id: Job identifier to pause.
    """
    store = ScheduledResearchJobStore()
    job = store.get(job_id)
    if job is None:
        console.print(f"[yellow]Job [cyan]{job_id}[/cyan] not found.[/yellow]")
        return EXIT_USAGE_ERROR

    if job.status == JobStatus.PAUSED:
        console.print(f"[dim]Job [cyan]{job_id}[/cyan] is already paused.[/dim]")
        return EXIT_SUCCESS

    job.status = JobStatus.PAUSED
    try:
        store.upsert(job)
    except Exception as exc:
        console.print(f"[red]Failed to pause job: {exc}[/red]")
        return EXIT_RUN_FAILED

    console.print(f"[yellow]Job [bold cyan]{job_id}[/bold cyan] paused.[/yellow]")
    return EXIT_SUCCESS


def cmd_schedule_resume(job_id: str) -> int:
    """Resume a paused scheduled research job.

    Resumed jobs are eligible for dispatch on the next daemon tick. If
    ``next_run_at`` is in the past they fire immediately.

    Args:
        job_id: Job identifier to resume.
    """
    store = ScheduledResearchJobStore()
    job = store.get(job_id)
    if job is None:
        console.print(f"[yellow]Job [cyan]{job_id}[/cyan] not found.[/yellow]")
        return EXIT_USAGE_ERROR

    if job.status != JobStatus.PAUSED:
        console.print(f"[dim]Job [cyan]{job_id}[/cyan] is not paused (status: {job.status.value}).[/dim]")
        return EXIT_SUCCESS

    job.status = JobStatus.PENDING
    try:
        store.upsert(job)
    except Exception as exc:
        console.print(f"[red]Failed to resume job: {exc}[/red]")
        return EXIT_RUN_FAILED

    console.print(f"[green]Job [bold cyan]{job_id}[/bold cyan] resumed.[/green]")
    if job.next_run_at <= int(time.time() * 1000):
        console.print("[dim]Job is overdue and will fire on the next daemon tick.[/dim]")
    return EXIT_SUCCESS
