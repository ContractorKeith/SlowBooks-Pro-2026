"""Server Edition groundwork (PR-S1): LAN bind plumbing, SQLite WAL
tuning, and the /api/system server_mode flag the UI keys its header off."""

import sqlite3

from sqlalchemy import create_engine, text

import desktop_launcher
from app.database import enable_sqlite_tuning

# ---------------------------------------------------------------------------
# SQLite concurrency tuning
# ---------------------------------------------------------------------------


def test_sqlite_tuning_enables_wal(tmp_path):
    db = tmp_path / "tuned.db"
    engine = create_engine(f"sqlite:///{db}")
    enable_sqlite_tuning(engine)
    with engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
        busy = conn.execute(text("PRAGMA busy_timeout")).scalar()
        sync = conn.execute(text("PRAGMA synchronous")).scalar()
    assert str(mode).lower() == "wal"
    assert int(busy) == 5000
    assert int(sync) == 1  # NORMAL

    # WAL is persistent in the file: a plain sqlite3 connection sees it too
    raw = sqlite3.connect(db)
    assert raw.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    raw.close()


def test_sqlite_tuning_harmless_on_memory_db():
    engine = create_engine("sqlite:///:memory:")
    enable_sqlite_tuning(engine)
    with engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
    assert str(mode).lower() == "memory"  # no-op, no error


# ---------------------------------------------------------------------------
# Launcher bind plumbing
# ---------------------------------------------------------------------------


def test_server_env_default_is_loopback():
    env = desktop_launcher._server_env("sqlite:///x.db", 3001)
    assert env["APP_HOST"] == "127.0.0.1"
    assert env["SLOWBOOKS_SERVER_MODE"] == "0"


def test_server_env_lan_bind_sets_server_mode():
    env = desktop_launcher._server_env("sqlite:///x.db", 3001, bind_host="0.0.0.0")
    assert env["APP_HOST"] == "0.0.0.0"
    assert env["SLOWBOOKS_SERVER_MODE"] == "1"
    env = desktop_launcher._server_env(
        "sqlite:///x.db", 3001, bind_host="192.168.68.50"
    )
    assert env["SLOWBOOKS_SERVER_MODE"] == "1"


def test_lan_addresses_never_raises():
    addrs = desktop_launcher._lan_addresses()
    assert isinstance(addrs, list)
    assert all(isinstance(a, str) and a for a in addrs)


# ---------------------------------------------------------------------------
# /api/system flag
# ---------------------------------------------------------------------------


def test_system_info_reports_server_mode(authed_client, monkeypatch):
    info = authed_client.get("/api/system").json()
    assert info["server_mode"] is False

    monkeypatch.setenv("SLOWBOOKS_SERVER_MODE", "1")
    info = authed_client.get("/api/system").json()
    assert info["server_mode"] is True


# ---------------------------------------------------------------------------
# PR-S4: banner composition + --data-dir override
# ---------------------------------------------------------------------------


def test_serve_banner_lists_all_addresses():
    text = desktop_launcher._compose_serve_banner(3001, ["OFFICE-PC", "192.168.68.50"])
    assert "http://OFFICE-PC:3001" in text
    assert "http://192.168.68.50:3001" in text
    assert "plain HTTP" in text
    # No addresses discovered: still renders something actionable
    fallback = desktop_launcher._compose_serve_banner(3001, [])
    assert "3001" in fallback


def test_data_dir_flag_redirects_everything(tmp_path):
    import subprocess
    import sys as _sys

    target = tmp_path / "server-data"
    r = subprocess.run(
        [
            _sys.executable,
            "desktop_launcher.py",
            "--setup-only",
            "--data-dir",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env={**__import__("os").environ, "SLOWBOOKS_DATA_DIR": ""},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert (target / "companies").is_dir()
