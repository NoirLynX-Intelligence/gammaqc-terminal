"""Config persistence + env-var override tests.

The config file is the only stateful surface in the CLI; if it leaks
API keys, mishandles paths, or silently overrides backend, users get
burned in real ways.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from gammaqc_terminal import config as config_module
from gammaqc_terminal.config import BACKEND_ENV, DEFAULT_BACKEND, Config


@pytest.fixture
def isolated_config_dir(tmp_path, monkeypatch):
    """Point the config module at a tmp dir so tests don't smear into
    the real user's ~/.config."""
    monkeypatch.setattr(config_module, "user_config_dir",
                        lambda *_a, **_kw: str(tmp_path))
    # Wipe any cached env override.
    monkeypatch.delenv(BACKEND_ENV, raising=False)
    return tmp_path


def test_load_defaults_when_no_config_file(isolated_config_dir):
    cfg = Config.load()
    assert cfg.api_key is None
    assert cfg.backend_url == DEFAULT_BACKEND
    assert cfg.pro_unlocked is False


def test_save_then_load_round_trip(isolated_config_dir):
    cfg = Config(api_key="sk-test-abc", backend_url="https://example.test", pro_unlocked=True)
    cfg.save()
    cfg2 = Config.load()
    assert cfg2.api_key == "sk-test-abc"
    assert cfg2.backend_url == "https://example.test"
    assert cfg2.pro_unlocked is True


def test_env_backend_overrides_file(isolated_config_dir, monkeypatch):
    cfg = Config(api_key="x", backend_url="https://file-says-this.test")
    cfg.save()
    monkeypatch.setenv(BACKEND_ENV, "https://env-says-this.test")
    cfg2 = Config.load()
    assert cfg2.backend_url == "https://env-says-this.test"
    # api_key from file must NOT be overridable by env (security boundary)
    assert cfg2.api_key == "x"


def test_clear_api_key_removes_key_and_pro_flag(isolated_config_dir):
    cfg = Config(api_key="sk-x", pro_unlocked=True)
    cfg.save()
    cfg.clear_api_key()
    cfg2 = Config.load()
    assert cfg2.api_key is None
    assert cfg2.pro_unlocked is False


def test_load_corrupt_file_falls_back_to_defaults(isolated_config_dir):
    (isolated_config_dir / "config.json").write_text("not valid json {{{")
    cfg = Config.load()
    assert cfg.api_key is None
    assert cfg.backend_url == DEFAULT_BACKEND


def test_extra_keys_preserved_round_trip(isolated_config_dir):
    cfg = Config(api_key="x", extra={"telemetry_opt_in": False, "theme": "dark"})
    cfg.save()
    cfg2 = Config.load()
    assert cfg2.extra.get("telemetry_opt_in") is False
    assert cfg2.extra.get("theme") == "dark"


def test_config_file_does_not_leak_into_stdout(isolated_config_dir, capsys):
    """Loading config must NEVER print to stdout — it's called from
    every command and would corrupt JSON output paths."""
    cfg = Config(api_key="sk-secret", pro_unlocked=True)
    cfg.save()
    _ = Config.load()
    captured = capsys.readouterr()
    assert "sk-secret" not in captured.out
    assert captured.out == ""
