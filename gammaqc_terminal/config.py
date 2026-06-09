"""Local config + API-key storage.

Honesty contract: API keys never leave the user's machine except in the
Authorization header to api.gammaqc.com over TLS. Stored at the OS-native
config dir (platformdirs) with 0600 perms on POSIX. No telemetry, no
analytics, no remote-mutable settings.
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir

APP_NAME = "gammaqc"
DEFAULT_BACKEND = "https://api.gammaqc.com"

# Backend can be overridden via env for self-hosted / staging — useful for
# the 500-domain mesh edge-routing tests and for CI smoke runs.
BACKEND_ENV = "GAMMAQC_BACKEND_URL"


def config_path() -> Path:
    """Per-user config file path. Created lazily."""
    p = Path(user_config_dir(APP_NAME, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p / "config.json"


@dataclass
class Config:
    api_key: str | None = None
    backend_url: str = DEFAULT_BACKEND
    # Pro-tier features unlock locally too — the wall is server-side. This
    # mirror is purely UX: tells the CLI to skip the lock-emojis when the
    # user has authenticated, so the surface feels seamless.
    pro_unlocked: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Config":
        # Env-var override always wins for backend_url (sovereign mesh /
        # staging). Never override api_key from env — too easy to leak into
        # CI logs or .bash_history accidentally.
        path = config_path()
        data: dict[str, Any] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                data = {}
        cfg = cls(
            api_key=data.get("api_key"),
            backend_url=os.environ.get(BACKEND_ENV) or data.get("backend_url", DEFAULT_BACKEND),
            pro_unlocked=bool(data.get("pro_unlocked", False)),
            extra={k: v for k, v in data.items()
                   if k not in {"api_key", "backend_url", "pro_unlocked"}},
        )
        return cfg

    def save(self) -> None:
        path = config_path()
        payload = {
            "api_key": self.api_key,
            "backend_url": self.backend_url,
            "pro_unlocked": self.pro_unlocked,
            **self.extra,
        }
        path.write_text(json.dumps(payload, indent=2))
        # 0600 — read/write owner only. Best-effort on Windows (no-op).
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def clear_api_key(self) -> None:
        self.api_key = None
        self.pro_unlocked = False
        self.save()
