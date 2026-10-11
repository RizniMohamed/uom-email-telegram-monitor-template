"""Read private configuration without importing or logging legacy settings."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values


class ConfigurationError(ValueError):
    """A configuration error whose message never includes the supplied value."""


@dataclass(frozen=True)
class Config:
    username: str = field(repr=False)
    password: str = field(repr=False)
    telegram_bot_token: str = field(repr=False)
    chat_id: str = field(repr=False)
    host: str = "mail.uom.lk"
    port: int = 993
    check_interval: int = 30
    retry_interval: int = 30
    state_file: Path = Path("uom_monitor_state.json")


def load_config(env_file=None, environ=None):
    env = dict(os.environ if environ is None else environ)
    source_root = Path(__file__).resolve().parents[2]
    base = source_root if (source_root / "pyproject.toml").is_file() else Path.cwd()
    path = Path(env_file or env.get("UOM_ENV_FILE") or base / ".env").expanduser().resolve()
    values = dict(dotenv_values(path, interpolate=False)) if path.is_file() else {}
    values.update(env)  # Explicit environment always wins, even when empty.

    def required(name):
        value = values.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(f"Missing required configuration: {name}")
        if any(c in value for c in ("\x00", "\r", "\n")):
            raise ConfigurationError(f"Invalid control character in {name}")
        return value

    def integer(name, default, maximum):
        try:
            number = int(values.get(name, default))
        except (TypeError, ValueError):
            raise ConfigurationError(f"{name} must be a positive integer") from None
        if not 1 <= number <= maximum:
            raise ConfigurationError(f"{name} must be between 1 and {maximum}")
        return number

    username = required("UOM_USERNAME")
    password = required("UOM_PASSWORD")
    token = required("TELEGRAM_BOT_TOKEN")
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise ConfigurationError("TELEGRAM_BOT_TOKEN has an invalid format")
    chat = required("TELEGRAM_CHAT_ID")
    if not re.fullmatch(r"-?[0-9]+", chat) or int(chat) == 0:
        raise ConfigurationError("TELEGRAM_CHAT_ID must be a nonzero numeric chat ID")
    host = values.get("UOM_IMAP_HOST", "mail.uom.lk")
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        raise ConfigurationError("UOM_IMAP_HOST must be a hostname")
    state = values.get("UOM_STATE_FILE", str(base / "uom_monitor_state.json"))
    if not isinstance(state, str) or not state.strip() or "\x00" in state:
        raise ConfigurationError("UOM_STATE_FILE must be a file path")
    state_path = Path(state).expanduser()
    if not state_path.is_absolute():
        state_path = path.parent / state_path
    state_path = state_path.resolve()
    if state_path.is_dir():
        raise ConfigurationError("UOM_STATE_FILE points to a directory")
    return Config(username, password, token, chat, host,
                  integer("UOM_IMAP_PORT", 993, 65535),
                  integer("CHECK_INTERVAL", 30, 86400),
                  integer("RETRY_INTERVAL", 30, 86400), state_path)
