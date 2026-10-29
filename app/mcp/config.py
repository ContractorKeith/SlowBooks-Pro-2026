# ============================================================================
# MCP server settings, from the environment. The token comes from a file and
# nowhere else: an argument or an environment variable would show up in a
# process list or an agent's config, and a file can be made private (0600).
# Error sentences name the problem and the fix, never the token's content.
# ============================================================================

import math
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

DEFAULT_URL = "http://127.0.0.1:3001"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ConfigError(ValueError):
    """A setting the server cannot start with; the message is the sentence."""


@dataclass(frozen=True)
class Config:
    base_url: str
    token: str = ""
    timeout: float = 120.0

    def __repr__(self) -> str:  # keep the token out of any log line
        return f"Config(base_url={self.base_url!r}, timeout={self.timeout})"


def _base_url(raw: str) -> str:
    sentence = (
        "SLOWBOOKS_URL must be a loopback http:// address, "
        "like http://127.0.0.1:3001"
    )
    try:
        parts = urlsplit(raw.strip())
        host, _ = parts.hostname, parts.port
    except ValueError:
        raise ConfigError(sentence) from None
    if (
        parts.scheme != "http"
        or host not in _LOOPBACK_HOSTS
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ConfigError(sentence)
    return raw.strip().rstrip("/")


def _read_token(raw_path: str) -> str:
    if not raw_path.strip():
        raise ConfigError(
            "SLOWBOOKS_TOKEN_FILE must name a file that holds the API token "
            "(create one under Settings, API Tokens)."
        )
    path = os.path.expanduser(raw_path.strip())
    if not os.path.isfile(path):
        raise ConfigError(f"The token file {path} does not exist.")
    # Windows reports 0o666 for every file, so the check means nothing there.
    if os.name != "nt" and os.stat(path).st_mode & 0o077:
        raise ConfigError(
            f"The token file {path} can be read by other users. "
            f"Run: chmod 600 {path}"
        )
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().strip().splitlines()
    except (OSError, UnicodeDecodeError):
        raise ConfigError(f"The token file {path} could not be read.") from None
    if len(lines) != 1 or not lines[0].strip().startswith("sbp_"):
        raise ConfigError(
            f"The token file {path} must hold one line: the API token, "
            "which starts with sbp_."
        )
    return lines[0].strip()


def _timeout(raw: str) -> float:
    sentence = "SLOWBOOKS_TIMEOUT_SECONDS must be a number from 1 to 3600."
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(sentence) from None
    if not math.isfinite(value) or not 1 <= value <= 3600:
        raise ConfigError(sentence)
    return value


def load_config() -> Config:
    base_url = _base_url(os.environ.get("SLOWBOOKS_URL") or DEFAULT_URL)
    token = _read_token(os.environ.get("SLOWBOOKS_TOKEN_FILE") or "")
    timeout = _timeout(os.environ.get("SLOWBOOKS_TIMEOUT_SECONDS") or "120")
    return Config(base_url=base_url, token=token, timeout=timeout)
