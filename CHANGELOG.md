# Changelog

## v0.2.0 — 2026-06-09

### Added
- `gamma shock --hedge` (`-H`) — Pro-tier flag that POSTs the Blast Radius
  Report to `/api/oracle/shock/hedge` and renders the **Algorithmic Hedge
  Strategy** inline. Per-position hedge action + instrument + sizing %
  + dollar notional + compliance-grade rationale. Backed by the
  deterministic backend rule table (no LLM call — same input always
  yields the same recommendation, reproducible in audit).
- `gammaqc_terminal.hedge` module — typed `HedgeRecommendation` +
  `HedgeResponse` dataclasses; `request_hedges()` function. Honest
  fail-modes: 402 returns a structured `HedgeResponse.error` (not an
  exception) so the locked CTA surfaces cleanly; 401/429/5xx raise
  `HedgeError` with distinct user-facing messages.
- 10 new tests covering hedge auth contract, 402/401/429/5xx error
  paths, malformed-row resilience, and the exact JSON shape posted to
  the backend (`/api/oracle/shock/hedge` contract pin).
- GitHub Actions:
  - `test.yml` — pytest matrix on Python 3.10/3.11/3.12 × ubuntu/macos/
    windows, plus a `package-build` job that validates sdist + wheel
    via `twine check`.
  - `publish.yml` — tag-triggered (`v*.*.*`) publish to PyPI. Hard
    gates: tag must be on main, tag version must match pyproject.toml,
    full test matrix must be green. Supports both Trusted Publisher
    (OIDC, preferred) and `PYPI_API_TOKEN` (legacy fallback).
  - `sovereign-pr-sentinel.yml` + `sps-self-tune.yml` — full SPS v2.0.8
    port from the backend repo. Multi-sentinel panel + adversarial
    verifier + coherence auditor with HMAC-SHA256 signed verdicts.

### Changed
- `__version__` → `0.2.0`, `__phase__` → `"terminal-v0.2"`.
- `gamma shock` (without `--hedge`) now shows a contextual hint when
  the caller IS Pro-unlocked: "Tip: add --hedge for per-position
  hedge recommendations." Locked footer unchanged for free callers.

### Backend contract
- Backend pinned at `ora-v2.2` (PR #29 merged + deployed).
- 5 endpoints live: `/api/oracle/auth/validate`, `/card/sealed`,
  `/voice/warren`, `/shock/hedge`, `/watch/receipt`.
- Single-flight + 60s LRU cache on `/voice/warren` (no parallel-CLI
  spend race).

---

## v0.1.1 — 2026-06-08

### Fixed
- CLI hardcoded `/oracle/*` paths → updated to `/api/oracle/*` to match
  backend FastAPI router prefix (`app.include_router(..., prefix="/api")`).

---

## v0.1.0 — 2026-06-08

Initial public release.

### Added
- `gamma analyze <TICKER>` — SEC + quote + Warren-Voice + Trader Card
- `gamma scrape <TICKER>` — raw JSON to stdout (pipe-friendly)
- `gamma card <TICKER>` — Trader Card render only
- `gamma shock -p <CSV> -e <EVENT>` — Portfolio Shock Matrix (Blast Radius Report)
- `gamma watch <TICKER> -t <EXPR>` — Headless Ghost-Watcher daemon
- `gamma login` / `logout` / `status` — auth + config commands
- Local-rules Warren-Voice (offline-safe; no API key needed)
- Privacy contract: holdings CSV NEVER leaves the user's machine in free mode
- install.sh with zero telemetry (only the package download)
- API keys stored at `platformdirs` config dir with 0600 perms
- Cross-platform: Python 3.10+, ubuntu/macos/windows
