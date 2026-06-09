"""Unit tests for Portfolio Shock Matrix.

Honesty contract: every test asserts ONE concrete behavior. No mock
verification of internal implementation details — only inputs/outputs
that a user would actually observe.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from gammaqc_terminal.shock import (
    EVENT_BETAS,
    TICKER_CLASS,
    _asset_class_for,
    _classify_event,
    load_holdings,
    run_shock,
)


def _write_csv(tmp_path: Path, rows: list[str]) -> Path:
    p = tmp_path / "h.csv"
    p.write_text("\n".join(rows))
    return p


def test_classify_event_keywords():
    assert _classify_event("Fed raises rates 50bps") == "fed_hike"
    assert _classify_event("CPI prints hot") == "cpi_hot"
    assert _classify_event("Russia invades again") == "geopolitical"
    assert _classify_event("nothing relevant") == "fed_hike"   # default


def test_asset_class_known_tickers():
    assert _asset_class_for({"ticker": "NVDA", "sector": ""}) == "growth_tech"
    assert _asset_class_for({"ticker": "JPM", "sector": ""}) == "financials"
    assert _asset_class_for({"ticker": "XOM", "sector": ""}) == "energy"


def test_asset_class_sector_hint_overrides_ticker_lookup():
    # If the CSV says "tech" we should trust it, not need a ticker entry
    assert _asset_class_for({"ticker": "PLTR", "sector": "tech"}) == "growth_tech"
    assert _asset_class_for({"ticker": "OXY",  "sector": "energy"}) == "energy"


def test_asset_class_unknown_ticker_no_sector_returns_unknown():
    assert _asset_class_for({"ticker": "ZZZZZ", "sector": ""}) == "unknown"


def test_load_holdings_value_column(tmp_path: Path):
    p = _write_csv(tmp_path, ["ticker,value", "NVDA,10000", "JPM,5000"])
    rows = load_holdings(p)
    assert len(rows) == 2
    assert rows[0]["ticker"] == "NVDA" and rows[0]["value"] == 10000.0


def test_load_holdings_qty_price_columns(tmp_path: Path):
    p = _write_csv(tmp_path, ["symbol,qty,price", "NVDA,100,80.5"])
    rows = load_holdings(p)
    assert rows[0]["ticker"] == "NVDA"
    assert rows[0]["value"] == pytest.approx(8050.0)


def test_load_holdings_strips_dollar_and_commas(tmp_path: Path):
    p = _write_csv(tmp_path, ["ticker,value", "NVDA,\"$12,500.50\""])
    rows = load_holdings(p)
    assert rows[0]["value"] == pytest.approx(12500.50)


def test_load_holdings_missing_ticker_column_raises(tmp_path: Path):
    p = _write_csv(tmp_path, ["foo,bar", "1,2"])
    with pytest.raises(ValueError, match="ticker"):
        load_holdings(p)


def test_load_holdings_skips_blank_ticker_rows(tmp_path: Path):
    p = _write_csv(tmp_path, ["ticker,value", "NVDA,1000", ",500", "JPM,2000"])
    rows = load_holdings(p)
    assert [r["ticker"] for r in rows] == ["NVDA", "JPM"]


def test_run_shock_fed_hike_growth_tech_bleeds(tmp_path: Path):
    p = _write_csv(tmp_path, ["ticker,value", "NVDA,10000"])
    report = run_shock(p, "Fed raises rates 50bps")
    assert report.event_class == "fed_hike"
    nvda = next(r for r in report.rows if r.ticker == "NVDA")
    assert nvda.beta == EVENT_BETAS["fed_hike"]["growth_tech"]
    assert nvda.blast_radius < 0   # bleeds
    assert nvda.direction == "BLEED"


def test_run_shock_fed_hike_financials_gain(tmp_path: Path):
    p = _write_csv(tmp_path, ["ticker,value", "JPM,5000"])
    report = run_shock(p, "Fed hike")
    jpm = next(r for r in report.rows if r.ticker == "JPM")
    assert jpm.blast_radius > 0
    assert jpm.direction == "GAIN"


def test_run_shock_sorts_bleeders_first(tmp_path: Path):
    p = _write_csv(tmp_path, ["ticker,value", "JPM,5000", "NVDA,10000"])
    report = run_shock(p, "Fed hike")
    # NVDA (bleeds) should come before JPM (gains)
    assert report.rows[0].ticker == "NVDA"
    assert report.rows[-1].ticker == "JPM"


def test_run_shock_unknown_ticker_neutral_and_warns(tmp_path: Path):
    p = _write_csv(tmp_path, ["ticker,value", "ZZZZZ,1000"])
    report = run_shock(p, "Fed hike")
    assert report.rows[0].asset_class == "unknown"
    assert report.rows[0].beta == 0.0
    assert any("NEUTRAL" in w for w in report.warnings)


def test_run_shock_net_blast_radius_sums_positions(tmp_path: Path):
    p = _write_csv(tmp_path, ["ticker,value", "NVDA,10000", "JPM,5000"])
    report = run_shock(p, "Fed hike")
    nvda_beta = EVENT_BETAS["fed_hike"]["growth_tech"]
    jpm_beta = EVENT_BETAS["fed_hike"]["financials"]
    expected = 10000 * nvda_beta + 5000 * jpm_beta
    assert report.net_blast_radius == pytest.approx(expected)


def test_known_ticker_map_intersects_event_betas():
    """Every asset class we map tickers to must exist in EVERY event-beta
    vector — otherwise unknown-event combinations silently default to 0.0
    and we lose signal. Catch this at test time, not at user runtime."""
    referenced_classes = set(TICKER_CLASS.values())
    for event, betas in EVENT_BETAS.items():
        missing = referenced_classes - set(betas.keys())
        assert not missing, f"event {event} missing betas for classes: {missing}"
