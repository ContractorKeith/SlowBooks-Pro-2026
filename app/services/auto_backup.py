# ============================================================================
# Automatic backups on a schedule the administrator sets in Settings (#651).
#
# The schedule lives under "schedule" in the backup settings file
# (backup_service): every N hours, daily at a time, or weekly on a day at a
# time, keeping the newest N automatic backups. A background thread started
# with the app checks once a minute. Times are this machine's local clock.
#
# Backups only run while the app is running. One that came due while it was
# closed runs within a minute of the next start (one backup, not one per
# missed slot). A failed one (the backup drive unplugged) is tried again an
# hour later and shown in Settings.
# ============================================================================

import logging
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from app.services import backup_service

logger = logging.getLogger(__name__)

FREQUENCIES = ("off", "hours", "daily", "weekly")
DEFAULT_SCHEDULE = {
    "frequency": "off",
    "every_hours": 24,
    "time": "23:00",
    "weekday": 6,  # Sunday (Monday is 0)
    "keep": 14,
}
RETRY_AFTER = timedelta(hours=1)
CHECK_EVERY_SECONDS = 60
LOCK_NAME = ".auto-backup.lock"
LOCK_STALE_AFTER = 2 * 3600  # seconds; older than this was left by a dead process


def get_schedule() -> dict:
    saved = backup_service.read_backup_settings().get("schedule")
    return {**DEFAULT_SCHEDULE, **(saved if isinstance(saved, dict) else {})}


def set_schedule(schedule: dict, now: datetime | None = None) -> dict:
    """Save the schedule. ``since`` marks when it was set: the first backup
    comes at the first scheduled time after that, not straight away."""
    now = now or datetime.now()
    saved = {k: schedule[k] for k in DEFAULT_SCHEDULE}
    saved["since"] = now.isoformat(timespec="seconds")
    backup_service.update_backup_settings(schedule=saved)
    return get_schedule()


def last_run() -> dict | None:
    """{"at", "ok", "message"} for the last automatic backup, or None."""
    last = backup_service.read_backup_settings().get("last_auto")
    return last if isinstance(last, dict) else None


def _slot_on_or_before(schedule: dict, t: datetime) -> datetime:
    """The latest daily/weekly scheduled moment at or before ``t``."""
    hh, mm = map(int, schedule["time"].split(":"))
    slot = t.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if schedule["frequency"] == "weekly":
        slot -= timedelta(days=(slot.weekday() - schedule["weekday"]) % 7)
        return slot if slot <= t else slot - timedelta(days=7)
    return slot if slot <= t else slot - timedelta(days=1)


def next_backup_at(settings: dict, now: datetime) -> datetime | None:
    """When the next automatic backup is due, from the settings file's
    contents, or None when they are off. A time already past is due now, so
    ``now`` is returned: one catch-up backup, not one per missed slot."""
    schedule = {**DEFAULT_SCHEDULE, **(settings.get("schedule") or {})}
    if schedule["frequency"] not in FREQUENCIES[1:] or not schedule.get("since"):
        return None
    since = datetime.fromisoformat(schedule["since"])
    ref = since
    last = settings.get("last_auto")
    if last:
        at = datetime.fromisoformat(last["at"])
        if at >= since:  # a run under an older schedule doesn't count
            if not last.get("ok"):
                return max(at + RETRY_AFTER, now)
            ref = at
    if schedule["frequency"] == "hours":
        due = ref + timedelta(hours=schedule["every_hours"])
    else:
        step = 7 if schedule["frequency"] == "weekly" else 1
        due = _slot_on_or_before(schedule, ref) + timedelta(days=step)
    return max(due, now)


# ---------------------------------------------------------------------------
# The lock. Under Docker two uvicorn workers each run the thread, and two
# apps can share one backup folder; the lock file in that folder makes sure
# only one takes a given backup.
# ---------------------------------------------------------------------------


def _new_token() -> str:
    return f"{os.getpid()}-{time.monotonic_ns()}"


def _try_create(path: Path) -> str | None:
    """Create the lock; returns the token written in it, or None."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return None
    token = _new_token()
    with os.fdopen(fd, "w") as f:  # for whoever wonders what holds it
        f.write(f"pid {os.getpid()}\n{datetime.now().isoformat()}\ntoken {token}\n")
    return token


def _release_lock(path: Path, token: str) -> None:
    """Remove the lock only if it is still ours: a lock reclaimed as stale
    and retaken by another worker must be left alone."""
    try:
        if f"token {token}\n" in path.read_text():
            path.unlink(missing_ok=True)
    except OSError:
        pass


def _age(path: Path) -> float | None:
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return None


def _acquire_lock(folder: Path) -> tuple[Path, str] | None:
    """(lock path, token) once taken, else None. A lock older than two
    hours is stale. To clear it the worker renames it to a name of its own:
    one rename wins and that worker alone deletes it and tries again; a
    failed rename means another worker got there first, so back off."""
    path = folder / LOCK_NAME
    token = _try_create(path)
    if token:
        return path, token
    age = _age(path)
    if age is None or age <= LOCK_STALE_AFTER:
        return None
    mine = folder / f"{LOCK_NAME}.stale-{_new_token()}"
    try:
        os.rename(path, mine)
    except OSError:
        return None
    age = _age(mine)
    if age is not None and age <= LOCK_STALE_AFTER:
        # We looked at the old lock but renamed a fresh one that another
        # worker made after clearing it: put it back if the name is free
        # (no hard links: FAT/exFAT drives lack them), else drop our copy.
        try:
            if not path.exists():
                os.rename(mine, path)
        except OSError:
            pass
        mine.unlink(missing_ok=True)
        return None
    mine.unlink(missing_ok=True)
    token = _try_create(path)
    return (path, token) if token else None


def run_if_due(now: datetime | None = None, session_factory=None) -> dict | None:
    """Take an automatic backup when one is due. Returns what was recorded
    as the last run, or None when nothing was due or another worker has it."""
    now = now or datetime.now()
    due = next_backup_at(backup_service.read_backup_settings(), now)
    if due is None or due > now:
        return None
    folder = backup_service.backup_dir()
    lock = None
    if folder.is_dir():
        lock = _acquire_lock(folder)  # (path, token)
        if lock is None:
            logger.info("Automatic backup skipped: another worker holds the lock")
            return None
    # A missing folder takes no lock: the backup below fails with its
    # sentence, which is what Settings shows.
    try:
        # another worker may have taken it between the check and the lock
        due = next_backup_at(backup_service.read_backup_settings(), now)
        if due is None or due > now:
            return None
        schedule = get_schedule()
        if session_factory is None:
            import app.database as database

            session_factory = database.SessionLocal
        db = session_factory()
        try:
            try:
                result = backup_service.create_backup(
                    db,
                    notes="Automatic backup",
                    backup_type="auto",
                    tag=backup_service.AUTO_TAG,
                )
                if result.get("success"):
                    backup_service.prune_auto_backups(db, schedule["keep"])
            except Exception:
                # recorded, so it is tried again in an hour, not every minute
                logger.exception("Automatic backup failed")
                result = {"success": False, "error": "Backup failed. Check logs."}
        finally:
            db.close()
        record = {
            "at": now.isoformat(timespec="seconds"),
            "ok": bool(result.get("success")),
            "message": result.get("filename") or result.get("error", "Backup failed"),
        }
        if record["ok"]:
            record["file_size"] = result.get("file_size")
        else:
            logger.warning("Automatic backup failed: %s", record["message"])
        backup_service.update_backup_settings(last_auto=record)
        return record
    finally:
        if lock is not None:
            _release_lock(*lock)


def _loop(stop: threading.Event) -> None:
    while not stop.wait(CHECK_EVERY_SECONDS):
        try:
            run_if_due()
        except Exception:
            logger.exception("Automatic backup check failed")


def start() -> threading.Event:
    """Start the background checker; set the returned event to stop it."""
    stop = threading.Event()
    threading.Thread(
        target=_loop, args=(stop,), name="auto-backup", daemon=True
    ).start()
    return stop
