"""gammaqc-terminal entrypoint — Typer-based CLI.

Five top-level commands:
    analyze    — pull SEC + quote, render Warren-Voice + Trader Card
    scrape     — raw scrape, JSON to stdout (pipe-friendly)
    card       — render Trader Card only (uses cached/passed analysis)
    shock      — Portfolio Shock Matrix (local CSV → Blast Radius Report)
    watch      — Ghost-Watcher daemon (foreground, Ctrl-C to stop)
    login      — bind to backend with API key
    logout     — clear local API key
    status     — show config + auth state

Every command prints a friendly help on `--help`. Free-tier commands
never block on network — backend is best-effort lift only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Reconfigure stdout/stderr to UTF-8 with replacement on encoding errors.
# Windows defaults to cp1252 which crashes on common glyphs (warning sign,
# right arrow, etc.) found in Rich panels. errors='replace' means a truly
# unsupported glyph degrades to '?' rather than aborting the whole render.
# Python 3.7+ has .reconfigure(); guard for safety on stranger streams.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

import typer  # noqa: E402  — must follow stdout reconfigure
from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402
from rich.text import Text  # noqa: E402

from . import __phase__, __version__
from .auth import AuthError, require_pro, validate_key
from .card import build_card_local, render_card, upgrade_card_via_backend
from .config import Config
from .hedge import HedgeError, request_hedges
from .scraper import scrape_ticker
from .shock import run_shock
from .voice import warren_analyze
from .watch import run_watch

# Windows note: legacy_windows=False forces Rich onto ANSI escape codes
# (universally supported by modern Windows terminals incl. Windows Terminal,
# PowerShell 7, ConEmu, VS Code) and avoids the cp1252-encoder crash that
# legacy_windows=True triggers when output contains non-Latin-1 glyphs.
# Falls back gracefully on truly ancient cmd.exe (chars degrade to '?').
console = Console(legacy_windows=False)
err_console = Console(stderr=True, legacy_windows=False)

app = typer.Typer(
    name="gamma",
    help="GammaQC sovereign quant terminal — free at the surface, PQC-sealed at the wall.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)


# ───────────────────────────── helpers ────────────────────────────────

def _print_locked_unlock_footer() -> None:
    """The Fiduciary Trap footer — printed once at the bottom of any
    free-tier command output. Suppressed when authenticated."""
    cfg = Config.load()
    if cfg.api_key and cfg.pro_unlocked:
        return
    msg = Text()
    msg.append("\n⚠ ", style="bold yellow")
    msg.append("Regulatory Audit Layer Locked.", style="bold yellow")
    msg.append("  To unlock the 10-Seat Council split and generate PQC-sealed compliance receipts, ")
    msg.append("authenticate your session: ", style="dim")
    msg.append("gamma login --api-key <KEY>", style="bold cyan")
    msg.append("  (Get your key at https://gammaqc.com/pro)", style="dim")
    console.print(msg)


# ───────────────────────────── commands ───────────────────────────────

@app.command()
def version() -> None:
    """Print version + phase string."""
    console.print(f"gammaqc-terminal [bold cyan]{__version__}[/]  phase=[bold]{__phase__}[/]")


@app.command()
def analyze(
    ticker: str = typer.Argument(..., help="Ticker symbol, e.g. NVDA"),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of rendered card"),
) -> None:
    """Pull SEC filings + quote, run Warren-Voice analysis, render Trader Card.

    Example: [bold]gamma analyze NVDA[/]
    """
    cfg = Config.load()
    with console.status(f"Scraping {ticker.upper()} (SEC + quote)…", spinner="dots"):
        scrape = scrape_ticker(ticker)
    if scrape.error and not scrape.quote and not scrape.recent_filings:
        err_console.print(f"[red]✗ {ticker}: {scrape.error}[/]")
        raise typer.Exit(code=2)

    with console.status("Running Warren-Voice structural analysis…", spinner="dots"):
        warren = warren_analyze(scrape.ticker, scrape.quote, scrape.recent_filings, cfg=cfg)

    if json_out:
        out = {
            "ticker": scrape.ticker, "cik": scrape.cik,
            "quote": scrape.quote, "recent_filings": scrape.recent_filings,
            "warren": {
                "bullets": warren.bullets, "bias": warren.bias,
                "confidence": warren.confidence, "source": warren.source,
                "attestation_hash": warren.attestation_hash,
            },
        }
        console.print_json(json.dumps(out))
        return

    card = build_card_local(scrape.ticker, warren)
    if cfg.api_key:
        card = upgrade_card_via_backend(card, cfg)
    locked = not (cfg.api_key and cfg.pro_unlocked and card.source == "backend")
    console.print(render_card(card, locked=locked))
    if warren.source == "backend-warren":
        console.print(f"[dim]Warren source: backend (attestation: {warren.attestation_hash or 'n/a'})[/]")
    if locked:
        _print_locked_unlock_footer()


@app.command()
def scrape(
    ticker: str = typer.Argument(..., help="Ticker symbol, e.g. NVDA"),
) -> None:
    """Raw scrape — emits JSON for piping into jq, scripts, dashboards."""
    result = scrape_ticker(ticker)
    out = {
        "ticker": result.ticker, "cik": result.cik,
        "quote": result.quote, "recent_filings": result.recent_filings,
        "error": result.error,
    }
    print(json.dumps(out, indent=2))   # raw stdout, not Rich-formatted


@app.command()
def card(
    ticker: str = typer.Argument(..., help="Ticker symbol, e.g. NVDA"),
) -> None:
    """Render the Trader Card only (no Warren bullets re-run on cached data)."""
    cfg = Config.load()
    with console.status(f"Building card for {ticker.upper()}…", spinner="dots"):
        scr = scrape_ticker(ticker)
        warren = warren_analyze(scr.ticker, scr.quote, scr.recent_filings, cfg=cfg)
        c = build_card_local(scr.ticker, warren)
        if cfg.api_key:
            c = upgrade_card_via_backend(c, cfg)
    locked = not (cfg.api_key and cfg.pro_unlocked and c.source == "backend")
    console.print(render_card(c, locked=locked))
    if locked:
        _print_locked_unlock_footer()


@app.command()
def shock(
    portfolio: Path = typer.Option(..., "--portfolio", "-p",
                                   help="Path to local holdings CSV",
                                   exists=True, file_okay=True, dir_okay=False, readable=True),
    event: str = typer.Option(..., "--event", "-e",
                              help="Plain-text event description, e.g. 'Fed raises rates 50bps'"),
    hedge: bool = typer.Option(False, "--hedge", "-H",
                               help="Pro-tier: request Algorithmic Hedge Strategy "
                                    "for the bleeders (requires `gamma login --api-key`)"),
) -> None:
    """Portfolio Shock Matrix — local Blast Radius Report.

    The CSV NEVER leaves your machine. Required columns: ticker (or symbol),
    and either value (or market_value) OR qty + price. Optional: sector.

    Example: [bold]gamma shock -p ./holdings.csv -e "Fed raises rates 50bps"[/]
    """
    cfg = Config.load()
    with console.status("Computing Blast Radius Report (local)…", spinner="dots"):
        report = run_shock(portfolio, event)

    header = Text()
    header.append("Event: ", style="bold dim")
    header.append(f"{report.event_text}  ", style="white")
    header.append(f"[{report.event_class}]", style="dim")
    console.print(Panel(header, border_style="white"))

    tbl = Table(show_header=True, header_style="bold magenta", expand=True)
    tbl.add_column("Ticker", style="bold cyan")
    tbl.add_column("Position $", justify="right")
    tbl.add_column("Asset Class", style="dim")
    tbl.add_column("Beta", justify="right", style="dim")
    tbl.add_column("Blast Radius", justify="right")
    tbl.add_column("Dir", style="bold")
    for r in report.rows:
        dir_color = "red" if r.blast_radius < 0 else ("green" if r.blast_radius > 0 else "dim")
        tbl.add_row(
            r.ticker,
            f"${r.position_value:,.0f}",
            r.asset_class,
            f"{r.beta:+.2f}",
            f"${r.blast_radius:+,.0f}",
            Text(r.direction, style=dir_color),
        )
    console.print(tbl)

    summary = Text()
    summary.append(f"\nTotal position: ${report.total_position:,.0f}\n", style="dim")
    net_color = "red" if report.net_blast_radius < 0 else ("green" if report.net_blast_radius > 0 else "dim")
    summary.append("Net Blast Radius: ", style="bold")
    summary.append(f"${report.net_blast_radius:+,.0f}", style=net_color)
    console.print(summary)

    for w in report.warnings:
        console.print(f"[yellow]⚠ {w}[/]")

    # v0.2: Pro-tier hedge generation
    if hedge:
        if not cfg.api_key:
            err_console.print("[red]✗ --hedge requires an API key. Run [bold]gamma login --api-key <KEY>[/].[/]")
            raise typer.Exit(code=3)
        try:
            with console.status("Requesting Algorithmic Hedge Strategy (backend Council)…", spinner="dots"):
                hedge_resp = request_hedges(report, cfg)
        except HedgeError as e:
            err_console.print(f"[red]✗ Hedge generation failed: {e}[/]")
            err_console.print("[dim]The local Blast Radius Report above is still actionable.[/]")
            raise typer.Exit(code=4) from e

        if hedge_resp.error:
            # Pro-required (402) — show the wall, don't crash
            err_console.print(f"[yellow]⚠ {hedge_resp.error}[/]")
            err_console.print("[dim]Upgrade at https://gammaqc.com/pro to unlock per-position hedges.[/]")
            return

        _render_hedge_response(hedge_resp)
        return

    # The Trap: Algorithmic Hedge Strategy is Pro-gated (locked CTA when --hedge not passed)
    if not (cfg.api_key and cfg.pro_unlocked):
        hedge_lock = Text()
        hedge_lock.append("\n🔒 ", style="bold red")
        hedge_lock.append("Deep-Layer Hedging Strategy", style="bold")
        hedge_lock.append(" requires 10-Seat Council Consensus.\n", style="white")
        hedge_lock.append("   Authenticate with ", style="dim")
        hedge_lock.append("gamma login --api-key <KEY>", style="bold cyan")
        hedge_lock.append(" to unlock per-position hedge generation, then re-run with ", style="dim")
        hedge_lock.append("--hedge", style="bold cyan")
        hedge_lock.append(".\n", style="dim")
        console.print(hedge_lock)
        _print_locked_unlock_footer()
    else:
        # Authed but didn't pass --hedge — gentle nudge
        console.print("[dim]\nTip: add [bold]--hedge[/] to this command for per-position hedge recommendations.[/]")


def _render_hedge_response(resp) -> None:
    """Pretty-print the backend Hedge response (Pro path). Separated so the
    shock() command stays scannable."""
    header = Text()
    header.append("\nAlgorithmic Hedge Strategy", style="bold magenta")
    header.append(f" — event_class=[{resp.event_class}]\n", style="dim")
    console.print(header)

    if not resp.hedges:
        console.print("[yellow]No hedgeable bleeders in this report.[/]")
        if resp.unhedgeable:
            console.print("[dim]Unhedgeable positions:[/]")
            for u in resp.unhedgeable:
                console.print(f"  - {u.get('ticker')} ({u.get('asset_class')}): {u.get('reason', '')}")
        return

    hedge_tbl = Table(show_header=True, header_style="bold magenta", expand=True)
    hedge_tbl.add_column("Ticker", style="bold cyan")
    hedge_tbl.add_column("Action", style="bold")
    hedge_tbl.add_column("Instrument")
    hedge_tbl.add_column("Size %", justify="right", style="dim")
    hedge_tbl.add_column("Notional", justify="right")
    for h in resp.hedges:
        hedge_tbl.add_row(
            h.ticker,
            h.hedge_action,
            h.instrument,
            f"{h.sizing_pct_of_position}%",
            f"${h.hedge_notional:,.0f}",
        )
    console.print(hedge_tbl)

    for h in resp.hedges:
        console.print(f"[dim]  {h.ticker}: {h.rationale}[/]")

    summary = Text()
    summary.append(f"\nTotal hedge notional: ${resp.total_hedge_notional:,.0f}\n", style="bold")
    if resp.attestation_hash:
        summary.append(f"PQC attestation: {resp.attestation_hash[:32]}…\n", style="dim green")
    if resp.unhedgeable:
        summary.append(f"\n{len(resp.unhedgeable)} unhedgeable position(s):\n", style="yellow")
        for u in resp.unhedgeable:
            summary.append(f"  - {u.get('ticker')} ({u.get('asset_class')}): {u.get('reason', '')}\n",
                           style="dim")
    # Compliance footer — last hedge's disclaimer (all hedges share the same boilerplate)
    if resp.hedges:
        summary.append(f"\n{resp.hedges[0].disclaimer}\n", style="dim italic")
    console.print(summary)


@app.command()
def watch(
    ticker: str = typer.Argument(..., help="Ticker symbol, e.g. NVDA"),
    trigger: str = typer.Option(..., "--trigger", "-t",
                                help="Trigger expression, e.g. 'pct_change < -3.0' "
                                     "or 'volume_spike > 3.0' or 'options_volume_spike > 3.0' (Pro)"),
    interval: int = typer.Option(30, "--interval", "-i", min=5, max=3600,
                                 help="Seconds between polls (5–3600)"),
    webhook: str | None = typer.Option(None, "--webhook", "-w",
                                       help="Optional webhook URL (Discord/Slack) to ping on fire"),
) -> None:
    """Ghost-Watcher — headless daemon. Detaches a polling loop; fires native
    desktop notification + optional webhook when trigger is satisfied.

    Example: [bold]gamma watch NVDA -t "volume_spike > 3.0" -i 60[/]
    """
    cfg = Config.load()
    try:
        console.print(f"[dim]Watching [bold]{ticker.upper()}[/] for [bold]{trigger}[/] every {interval}s. "
                      f"Ctrl-C to stop.[/]")
        run_watch(ticker, trigger, interval_s=interval, webhook_url=webhook, cfg=cfg,
                  on_event=lambda m: console.print(f"[dim]{m}[/]"))
        console.print("[green]✓ Trigger fired — desktop notification sent.[/]")
        if not (cfg.api_key and cfg.pro_unlocked):
            trap = Text()
            trap.append("\n🔒 ", style="bold red")
            trap.append("Autonomous receipt generation", style="bold")
            trap.append(" (PQC-sealed risk log auto-emitted on every fire) ", style="white")
            trap.append("requires a Pro API key.\n", style="white")
            trap.append("   Authenticate: ", style="dim")
            trap.append("gamma login --api-key <KEY>", style="bold cyan")
            console.print(trap)
    except ValueError as e:
        err_console.print(f"[red]✗ {e}[/]")
        raise typer.Exit(code=2) from e
    except KeyboardInterrupt:
        console.print("\n[yellow]Watcher stopped.[/]")


@app.command()
def login(
    api_key: str = typer.Option(..., "--api-key", "-k", prompt=True, hide_input=True,
                                help="GammaQC API key. Get one at https://gammaqc.com/pro"),
    backend: str | None = typer.Option(None, "--backend",
                                       help="Override backend URL (advanced)"),
) -> None:
    """Bind this terminal to your GammaQC API key. Validates against backend."""
    cfg = Config.load()
    cfg.api_key = api_key.strip()
    if backend:
        cfg.backend_url = backend.strip().rstrip("/")
    try:
        with console.status("Validating key with backend…", spinner="dots"):
            info = validate_key(cfg)
        tier = (info.get("tier") or "free").lower()
        cfg.pro_unlocked = tier in {"pro", "enterprise"}
        cfg.save()
        console.print(f"[green]✓ Authenticated.[/] Tier: [bold]{tier}[/]  "
                      f"Backend: [dim]{cfg.backend_url}[/]")
        if not cfg.pro_unlocked:
            console.print("[yellow]Note: account is free-tier. Pro-gated commands "
                          "(hedge generation, sealed receipts) will return 402.[/]")
    except AuthError as e:
        err_console.print(f"[red]✗ {e}[/]")
        # Do NOT persist a key that failed validation
        cfg.clear_api_key()
        raise typer.Exit(code=3) from e


@app.command()
def logout() -> None:
    """Clear the local API key. Backend session not affected (keys are stateless)."""
    cfg = Config.load()
    cfg.clear_api_key()
    console.print("[green]✓ Logged out.[/] Local config cleared.")


@app.command()
def status() -> None:
    """Show current config + authentication state. No network calls."""
    cfg = Config.load()
    masked = (cfg.api_key[:6] + "…" + cfg.api_key[-4:]) if cfg.api_key else "[red](not set)[/]"
    tbl = Table(show_header=False, expand=False)
    tbl.add_column("Field", style="bold dim")
    tbl.add_column("Value")
    tbl.add_row("Version", __version__)
    tbl.add_row("Phase", __phase__)
    tbl.add_row("Backend", cfg.backend_url)
    tbl.add_row("API Key", masked)
    tbl.add_row("Pro Unlocked", "[green]yes[/]" if cfg.pro_unlocked else "[yellow]no[/]")
    console.print(tbl)


if __name__ == "__main__":   # pragma: no cover
    app()
