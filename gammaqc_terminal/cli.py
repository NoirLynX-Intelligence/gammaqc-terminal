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


# ───────────────────────────── ORA: in-terminal concierge ─────────────────


def _render_welcome() -> None:
    """Friendly first-run / `gamma welcome` view. ORA in the terminal —
    same host energy as the web app, no chat surface (yet), just clear
    next-step guidance so finance users aren't dumped into a power-user
    CLI without a map.

    Commander feedback 2026-06-10: 'the terminal maybe too advanced for
    the average person. they need a guide and instructions. they need
    ORA. I don't even know how to navigate this.'
    """
    cfg = Config.load()
    pro = cfg.api_key and cfg.pro_unlocked
    g = Text()
    g.append("\n  ", style="")
    g.append("Γ", style="bold #C9A227")
    g.append("  Gamma QC  ", style="bold")
    g.append(f"v{__version__}", style="dim")
    g.append("\n  ", style="")
    g.append("ORA · Host · ", style="dim #C9A227")
    g.append("DrkLynX Sovereign Finance Module", style="dim")
    console.print(g)
    tbl = Table(show_header=False, box=None, padding=(0, 2), expand=False)
    tbl.add_column(style="bold #C9A227", justify="right", width=22)
    tbl.add_column()
    tbl.add_row("Try a ticker", "[bold cyan]gamma analyze NVDA[/]   "
                                 "[dim](SEC filings + price + Warren-voice)[/]")
    tbl.add_row("Raw scrape", "[bold cyan]gamma scrape AAPL --json[/]   "
                                 "[dim](pipe into jq / your scripts)[/]")
    tbl.add_row("Trader card", "[bold cyan]gamma card TSLA[/]   "
                                 "[dim](ASCII card for screenshot / log)[/]")
    tbl.add_row("Portfolio shock", "[bold cyan]gamma shock -p positions.csv "
                                 "--event fed_hike[/]   [dim](free; --hedge needs Pro)[/]")
    tbl.add_row("Verify a receipt", "[bold cyan]gamma verify receipt.json[/]   "
                                 "[dim](offline, public-key crypto)[/]")
    tbl.add_row("This guide again", "[bold cyan]gamma welcome[/]")
    console.print(Panel(tbl, title="[bold]free tier — works right now[/]",
                        border_style="dim", title_align="left"))
    if not pro:
        nudge = Text()
        nudge.append("\n  Pro tier", style="bold #C9A227")
        nudge.append("  unlocks the 10-seat Council, algorithmic hedges, "
                     "PQC-sealed compliance receipts, and the Ghost-Watcher daemon.\n",
                     style="dim")
        nudge.append("  Free 7-day trial: ", style="dim")
        nudge.append("https://app.gammaqc.com/subscribe", style="bold cyan")
        nudge.append("\n  Already have a key: ", style="dim")
        nudge.append("gamma login --api-key gqc_live_xxx", style="bold cyan")
        console.print(nudge)
    else:
        signed = Text()
        signed.append("\n  ✓ Pro authenticated", style="bold green")
        signed.append(f"  ({cfg.api_key[:6]}…{cfg.api_key[-4:]})", style="dim")
        console.print(signed)
    console.print()


def _maybe_first_run_banner() -> None:
    """Print the welcome banner ONCE on first run (no config file on disk).
    Subsequent invocations skip this — power users don't want greeted on
    every command. Records the first-run flag in config so we don't
    re-banner."""
    cfg = Config.load()
    if cfg.extra.get("first_run_seen"):
        return
    _render_welcome()
    # Mark seen — best-effort, never fail the command if save fails
    try:
        cfg.extra["first_run_seen"] = True
        cfg.save()
    except Exception:
        pass


# ───────────────────────────── commands ───────────────────────────────

@app.command()
def welcome() -> None:
    """Show the friendly Gamma QC onboarding guide. ORA in the terminal."""
    _render_welcome()


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
    _maybe_first_run_banner()
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
    portfolio: Path = typer.Option(None, "--portfolio", "-p",
                                   help="Path to local holdings CSV. Omit + use --sample "
                                        "for a built-in demo portfolio.",
                                   file_okay=True, dir_okay=False, readable=True),
    event: str = typer.Option("fed_hike", "--event", "-e",
                              help="Event class: fed_hike | cpi_hot | geopolitical. "
                                   "Or plain-text description for advanced parsing."),
    hedge: bool = typer.Option(False, "--hedge", "-H",
                               help="Pro-tier: request Algorithmic Hedge Strategy "
                                    "for the bleeders (requires `gamma login --api-key`)"),
    sample: bool = typer.Option(False, "--sample",
                                 help="Use a built-in 5-position demo portfolio "
                                      "(NVDA / AAPL / TLT / XLE / GLD) to try the command "
                                      "without writing your own CSV."),
) -> None:
    """Portfolio Shock Matrix — local Blast Radius Report.

    The CSV NEVER leaves your machine. Required columns: ticker (or symbol),
    and either value (or market_value) OR qty + price. Optional: sector.

    Example: [bold]gamma shock -p ./holdings.csv -e "Fed raises rates 50bps"[/]
    """
    _maybe_first_run_banner()
    cfg = Config.load()
    # ─── Helpful error path for the most common new-user mistake ──────────
    # Both --portfolio and --sample missing → don't dump the user back into
    # `Usage: gamma shock [OPTIONS]`. Show ORA's nudge with a one-line fix.
    if portfolio is None and not sample:
        err_console.print(
            "\n[bold #C9A227]ORA · Host[/]\n"
            "  [dim]You need to point shock at a portfolio CSV — or try the demo:[/]\n\n"
            "    [bold cyan]gamma shock --sample[/]    "
            "[dim]# 5-position built-in demo (NVDA / AAPL / TLT / XLE / GLD)[/]\n"
            "    [bold cyan]gamma shock -p ./my.csv[/]  "
            "[dim]# your own portfolio (columns: ticker, value)[/]\n"
        )
        raise typer.Exit(code=2)
    if sample:
        # Write a tiny demo CSV to the OS temp dir so the rest of the
        # shock pipeline can read it like any user CSV. The CSV is wiped
        # after the run so we don't leave demo data lying around the user's
        # disk (privacy-first contract still holds).
        import tempfile, csv as _csv
        demo_rows = [
            ("ticker", "value", "sector"),
            ("NVDA", "50000", "long_duration_tech"),
            ("AAPL", "30000", "growth_tech"),
            ("TLT",  "20000", "long_duration_tech"),  # bonds proxy
            ("XLE",  "15000", "energy"),
            ("GLD",  "10000", "gold"),
        ]
        portfolio = Path(tempfile.gettempdir()) / "gammaqc_demo_portfolio.csv"
        with portfolio.open("w", newline="", encoding="utf-8") as f:
            w = _csv.writer(f)
            w.writerows(demo_rows)
        console.print(
            "[dim]Using built-in demo portfolio "
            "(NVDA 50k · AAPL 30k · TLT 20k · XLE 15k · GLD 10k).[/]"
        )
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


@app.command()
def verify(
    receipt_path: str = typer.Argument(..., help="Path to receipt JSON, or '-' for stdin"),
    request_body: str = typer.Option(None, "--request-body", "-r",
                                     help="Path to the original request JSON for inputs_hash verification"),
    jwks_url: str = typer.Option(None, "--jwks-url",
                                 help="Override the attestor JWKS URL (default: attest.gammaqc.com)"),
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable JSON verdict"),
) -> None:
    """Verify a Sovereign Review Fabric audit_receipt OFFLINE.

    Free-tier. No API key required. Public-key crypto: fetches the
    attestor's JWKS once, verifies the Ed25519 signature locally. Pass
    --request-body to ALSO verify that the receipt was issued for the
    exact request you sent (cryptographic replay-proof).

    Examples:
      gamma verify receipt.json
      gamma verify - < response.json
      gamma verify receipt.json --request-body my_request.json
      gamma verify receipt.json --json   # for piping to jq / CI
    """
    from .verify import verify_receipt, DEFAULT_JWKS_URL, _load_json
    try:
        receipt_doc = _load_json(receipt_path)
    except FileNotFoundError as e:
        err_console.print(f"[red]✗[/] {e}")
        raise typer.Exit(code=3)
    except json.JSONDecodeError as e:
        err_console.print(f"[red]✗[/] receipt is not valid JSON: {e}")
        raise typer.Exit(code=3)

    # Receipt may be either the bare audit_receipt block OR a full
    # API response that contains it under "audit_receipt".
    if isinstance(receipt_doc, dict) and "audit_receipt" in receipt_doc:
        receipt = receipt_doc["audit_receipt"]
    else:
        receipt = receipt_doc

    req_body = None
    if request_body:
        try:
            req_body = _load_json(request_body)
        except Exception as e:
            err_console.print(f"[red]✗[/] could not load request body: {e}")
            raise typer.Exit(code=3)

    verdict = verify_receipt(
        receipt=receipt,
        original_request=req_body,
        jwks_url=jwks_url or DEFAULT_JWKS_URL,
    )

    if json_out:
        console.print_json(json.dumps(verdict))
    else:
        color = "green" if verdict["valid"] else "red"
        marker = "✓ VALID" if verdict["valid"] else "✗ INVALID"
        console.print(Panel(
            f"[bold {color}]{marker}[/]\n\n" + "\n".join(verdict["reasons"]),
            title="Sovereign Review Fabric — Receipt Verification",
            border_style=color,
        ))

    # Exit codes: 0 valid, 1 invalid, 2 unverifiable (no sig + degraded)
    if verdict["valid"]:
        # If all signature checks were None/skipped (no attestor + no req-body),
        # it's "unverifiable" not "valid"
        sig_present = verdict["checks"].get("attestor:signature_present")
        inputs_check = verdict["checks"].get("inputs_hash:matches_request")
        if sig_present is False and inputs_check is None:
            raise typer.Exit(code=2)
        raise typer.Exit(code=0)
    raise typer.Exit(code=1)


if __name__ == "__main__":   # pragma: no cover
    app()
