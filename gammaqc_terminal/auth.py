"""Backend auth — binds CLI to the sovereign GammaQC API.

Two contracts:
1. API key validation happens server-side via /oracle/auth/validate.
   Local pro_unlocked flag mirrors the LAST server verdict for UX only.
2. All Pro-gated calls revalidate server-side. The client cannot lie
   about Pro status — the wall is the wall.
"""
from __future__ import annotations

import httpx
from rich.console import Console

from .config import Config

console = Console()


class AuthError(Exception):
    pass


def _client(cfg: Config, *, timeout: float = 15.0) -> httpx.Client:
    headers = {
        "user-agent": "gammaqc-terminal/0.1",
        "accept": "application/json",
    }
    if cfg.api_key:
        headers["authorization"] = f"Bearer {cfg.api_key}"
    return httpx.Client(base_url=cfg.backend_url, headers=headers, timeout=timeout)


def validate_key(cfg: Config) -> dict:
    """Hit /oracle/auth/validate. Returns tier info on success; raises on
    failure. Never auto-mutates cfg — caller decides what to persist."""
    if not cfg.api_key:
        raise AuthError("no api key set — run `gamma login --api-key <KEY>`")
    with _client(cfg) as c:
        try:
            r = c.post("/oracle/auth/validate")
        except httpx.HTTPError as e:
            raise AuthError(f"backend unreachable at {cfg.backend_url}: {e}") from e
    if r.status_code == 401:
        raise AuthError("api key rejected by backend (401)")
    if r.status_code >= 400:
        raise AuthError(f"backend returned {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError as e:
        raise AuthError(f"backend returned non-JSON: {r.text[:200]}") from e


def require_pro(cfg: Config) -> dict:
    """Used by Pro-gated commands. Calls /oracle/auth/validate and confirms
    tier in {pro, enterprise}. Raises AuthError on free/missing/expired."""
    info = validate_key(cfg)
    tier = (info.get("tier") or "").lower()
    if tier not in {"pro", "enterprise"}:
        raise AuthError(
            f"this command requires Pro tier (current: {tier or 'free/unknown'}). "
            "Upgrade at https://gammaqc.com/pro"
        )
    return info
