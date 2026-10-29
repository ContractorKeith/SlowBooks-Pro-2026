"""Automatic backups on a schedule (#651).

The logic is tested with explicit ``now`` values and the settings file in a
temp folder; nothing sleeps. One test starts and stops the real thread.
"""

# ruff: noqa: F811 (the books fixture is imported, then named as an argument)

import os
import threading
import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.backups import Backup
from app.services import auto_backup, backup_service
from tests.test_backups_per_company import (  # noqa: F401 (fixture)
    _backup,
    _listed,
    books,
)

# 2026-10-28 is a Wednesday (weekday 2)
SINCE = datetime(2026, 10, 28, 10, 0, 0)


def _schedule(**over):
    schedule = {**auto_backup.DEFAULT_SCHEDULE, **over}
    auto_backup.set_schedule(schedule, now=SINCE)
    return schedule


def _next(now):
    return auto_backup.next_backup_at(backup_service.read_backup_settings(), now)


def _autos(books):
    return sorted(p.name for p in books.backups.glob("*-auto*.db"))


def test_off_is_never_due(books):
    _schedule(frequency="off")
    assert _next(SINCE + timedelta(days=400)) is None
    assert auto_backup.run_if_due(SINCE + timedelta(days=400)) is None
    assert _autos(books) == []


def test_nothing_is_due_before_a_schedule_is_set(books):
    assert _next(SINCE) is None


def test_daily_first_backup_is_the_next_slot_not_now(books):
    _schedule(frequency="daily", time="23:00")
    # set at 10:00, so the first is tonight; asked at 10:00 it is not yet due
    assert _next(SINCE) == datetime(2026, 10, 28, 23, 0)
    assert auto_backup.run_if_due(SINCE) is None
    # a slot earlier today than the moment it was set is tomorrow's
    _schedule(frequency="daily", time="09:00")
    assert _next(SINCE) == datetime(2026, 10, 29, 9, 0)


def test_daily_moves_on_a_day_after_a_run(books):
    _schedule(frequency="daily", time="23:00")
    ran = datetime(2026, 10, 28, 23, 0, 5)
    backup_service.update_backup_settings(
        last_auto={"at": ran.isoformat(), "ok": True, "message": "x.db"}
    )
    assert _next(ran) == datetime(2026, 10, 29, 23, 0)


def test_weekly_uses_the_weekday_slot(books):
    _schedule(frequency="weekly", weekday=4, time="02:30")  # Friday
    assert _next(SINCE) == datetime(2026, 10, 30, 2, 30)
    _schedule(frequency="weekly", weekday=2, time="09:00")  # Wednesday, passed
    assert _next(SINCE) == datetime(2026, 11, 4, 9, 0)
    _schedule(frequency="weekly", weekday=2, time="11:00")  # Wednesday, ahead
    assert _next(SINCE) == datetime(2026, 10, 28, 11, 0)


def test_hours_count_from_the_last_run(books):
    _schedule(frequency="hours", every_hours=6)
    assert _next(SINCE) == SINCE + timedelta(hours=6)
    ran = SINCE + timedelta(hours=7)
    backup_service.update_backup_settings(
        last_auto={"at": ran.isoformat(), "ok": True, "message": "x.db"}
    )
    assert _next(ran) == ran + timedelta(hours=6)


def test_a_run_under_an_older_schedule_does_not_count(books):
    _schedule(frequency="hours", every_hours=6)
    old = SINCE - timedelta(days=3)
    backup_service.update_backup_settings(
        last_auto={"at": old.isoformat(), "ok": False, "message": "old"}
    )
    assert _next(SINCE) == SINCE + timedelta(hours=6)


def test_a_missed_run_is_due_at_once_and_takes_one_backup(books, TestSession):
    _schedule(frequency="daily", time="23:00")
    week_later = SINCE + timedelta(days=7, hours=3)  # seven slots missed
    assert _next(week_later) == week_later
    record = auto_backup.run_if_due(week_later, session_factory=TestSession)
    assert record["ok"] is True
    assert len(_autos(books)) == 1
    # and the next one is the slot after this run, not another catch-up
    assert auto_backup.run_if_due(week_later, session_factory=TestSession) is None
    assert len(_autos(books)) == 1
    assert _next(week_later) == datetime(2026, 11, 4, 23, 0)


def test_a_failure_is_tried_again_an_hour_later(books, TestSession, monkeypatch):
    _schedule(frequency="daily", time="23:00")
    now = datetime(2026, 10, 29, 23, 5)
    monkeypatch.setattr(
        backup_service,
        "create_backup",
        lambda *a, **k: {"success": False, "error": "Drive not connected."},
    )
    record = auto_backup.run_if_due(now, session_factory=TestSession)
    assert record == {
        "at": now.isoformat(),
        "ok": False,
        "message": "Drive not connected.",
    }
    assert auto_backup.last_run() == record
    assert _next(now) == now + timedelta(hours=1)
    assert (
        auto_backup.run_if_due(now + timedelta(minutes=59), session_factory=TestSession)
        is None
    )
    assert auto_backup.run_if_due(now + timedelta(hours=1), session_factory=TestSession)


def test_a_due_run_makes_an_auto_backup_and_prunes_only_autos(
    books, client, TestSession, db_session
):
    manual = _backup(client)
    safety = "harbor-light-bakery_20260101_000000-before-restore.db"
    (books.backups / safety).write_bytes((books.backups / manual).read_bytes())
    old = []
    for day in (1, 2, 3, 4):
        name = f"harbor-light-bakery_2026090{day}_120000-auto.db"
        (books.backups / name).write_bytes((books.backups / manual).read_bytes())
        db_session.add(Backup(filename=name, file_size=1, backup_type="auto"))
        old.append(name)
    db_session.commit()
    _schedule(frequency="daily", time="23:00", keep=2)

    now = SINCE + timedelta(days=2)
    record = auto_backup.run_if_due(now, session_factory=TestSession)
    assert record["ok"] is True
    new = record["message"]
    assert backup_service.parse_backup_name(new)["tag"] == "auto"
    assert db_session.query(Backup).filter_by(filename=new).one().backup_type == "auto"

    # the new one and the newest old one stay; the rest are gone, file and row
    assert _autos(books) == sorted([old[3], new])
    left = {b.filename for b in db_session.query(Backup).all()}
    assert not set(old[:3]) & left and {old[3], new} <= left
    # manual and before-restore files are untouched
    assert (books.backups / manual).exists() and (books.backups / safety).exists()
    assert set(_listed(client)) >= {manual, safety, new, old[3]}


def test_a_failed_backup_prunes_nothing(books, TestSession, db_session, monkeypatch):
    for day in (1, 2, 3):
        (
            books.backups / f"harbor-light-bakery_2026090{day}_120000-auto.db"
        ).write_bytes(b"x")
    _schedule(frequency="daily", time="23:00", keep=1)
    monkeypatch.setattr(
        backup_service,
        "create_backup",
        lambda *a, **k: {"success": False, "error": "no"},
    )
    record = auto_backup.run_if_due(
        SINCE + timedelta(days=2), session_factory=TestSession
    )
    assert record["ok"] is False
    assert len(_autos(books)) == 3


def test_a_backup_that_raises_is_recorded_not_raised(books, TestSession, monkeypatch):
    _schedule(frequency="daily", time="23:00")

    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(backup_service, "create_backup", boom)
    record = auto_backup.run_if_due(
        SINCE + timedelta(days=2), session_factory=TestSession
    )
    assert record["ok"] is False and "disk on fire" not in record["message"]
    assert not (books.backups / auto_backup.LOCK_NAME).exists()


def test_a_missing_folder_fails_with_its_sentence(books, TestSession, tmp_path):
    _schedule(frequency="daily", time="23:00")
    backup_service.update_backup_settings(folder=str(tmp_path / "unplugged"))
    record = auto_backup.run_if_due(
        SINCE + timedelta(days=2), session_factory=TestSession
    )
    assert record["ok"] is False and "is not available" in record["message"]


def test_a_held_lock_means_skip(books, TestSession):
    _schedule(frequency="daily", time="23:00")
    lock = books.backups / auto_backup.LOCK_NAME
    lock.write_text("someone else")
    assert (
        auto_backup.run_if_due(SINCE + timedelta(days=2), session_factory=TestSession)
        is None
    )
    assert _autos(books) == [] and lock.read_text() == "someone else"


def test_the_lock_is_released_and_names_its_holder(books, TestSession):
    folder = books.backups
    lock = auto_backup._acquire_lock(folder)
    assert lock == folder / auto_backup.LOCK_NAME
    assert lock.read_text().startswith(f"pid {os.getpid()}\n")
    lock.unlink()
    _schedule(frequency="daily", time="23:00")
    auto_backup.run_if_due(SINCE + timedelta(days=2), session_factory=TestSession)
    assert not lock.exists()


def _stale(folder):
    lock = folder / auto_backup.LOCK_NAME
    lock.write_text("dead")
    old = time.time() - auto_backup.LOCK_STALE_AFTER - 60
    os.utime(lock, (old, old))
    return lock


def test_a_stale_lock_is_reclaimed_by_exactly_one_of_two(books):
    lock = _stale(books.backups)
    first = auto_backup._acquire_lock(books.backups)
    second = auto_backup._acquire_lock(books.backups)
    assert first == lock and second is None
    assert lock.read_text().startswith("pid ")
    assert not list(books.backups.glob(".auto-backup.lock.stale-*"))


def test_two_threads_racing_for_a_stale_lock_one_wins(books):
    _stale(books.backups)
    wins = []
    gate = threading.Barrier(2)

    def contend():
        gate.wait()
        wins.append(auto_backup._acquire_lock(books.backups))

    threads = [threading.Thread(target=contend) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len([w for w in wins if w is not None]) == 1
    assert not list(books.backups.glob(".auto-backup.lock.stale-*"))


def test_losing_the_stale_rename_backs_off(books, monkeypatch):
    lock = _stale(books.backups)

    def lost(src, dst):
        raise FileNotFoundError(src)  # the other worker renamed it first

    monkeypatch.setattr(os, "rename", lost)
    assert auto_backup._acquire_lock(books.backups) is None
    assert lock.exists()


def test_a_fresh_lock_is_not_taken_over(books):
    lock = books.backups / auto_backup.LOCK_NAME
    lock.write_text("busy")
    assert auto_backup._acquire_lock(books.backups) is None
    assert lock.read_text() == "busy"


def test_the_thread_starts_and_stops(books):
    stop = auto_backup.start()
    try:
        names = [t.name for t in threading.enumerate()]
        assert "auto-backup" in names
    finally:
        stop.set()
    for t in threading.enumerate():
        if t.name == "auto-backup":
            t.join(1)
    assert "auto-backup" not in [t.name for t in threading.enumerate() if t.is_alive()]


# ---- the routes ----


def _put(client, **over):
    body = {
        "frequency": "daily",
        "every_hours": 6,
        "time": "21:30",
        "weekday": 1,
        "keep": 7,
    }
    return client.put("/api/backups/schedule", json={**body, **over})


def test_the_schedule_is_saved_and_read_back(books, client):
    r = _put(client)
    assert r.status_code == 200, r.text
    got = client.get("/api/backups/schedule").json()
    assert got["frequency"] == "daily" and got["time"] == "21:30" and got["keep"] == 7
    assert got["every_hours"] == 6 and got["weekday"] == 1
    assert got["running_in_app"] is True
    assert got["last_run"] is None
    assert datetime.fromisoformat(got["next_run"]) > datetime.now() - timedelta(
        minutes=1
    )
    # kept beside the folder choice, not in the books
    assert backup_service.read_backup_settings()["schedule"]["keep"] == 7


def test_get_reports_the_last_run_and_off_has_no_next(books, client):
    assert client.get("/api/backups/schedule").json()["next_run"] is None
    _put(client)
    last = {
        "at": "2026-10-29T23:00:05",
        "ok": True,
        "message": "x.db",
        "file_size": 2048,
    }
    backup_service.update_backup_settings(last_auto=last)
    got = client.get("/api/backups/schedule").json()
    assert got["last_run"] == last


@pytest.mark.parametrize(
    "bad",
    [
        {"time": "25:00"},
        {"time": "9:30"},
        {"time": "noon"},
        {"keep": 0},
        {"keep": 366},
        {"every_hours": 0},
        {"every_hours": 169},
        {"weekday": 7},
        {"frequency": "monthly"},
        {"surprise": 1},
    ],
)
def test_a_bad_schedule_is_refused(books, client, bad):
    assert _put(client, **bad).status_code == 422
    assert "schedule" not in backup_service.read_backup_settings()


def _as(client, role):
    r = client.post("/api/tokens", json={"label": f"as-{role}", "role": role})
    assert r.status_code == 201, r.text
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return c


def test_only_an_administrator_reads_or_sets_the_schedule(books, client):
    for role in ("readonly", "bookkeeper"):
        other = _as(client, role)
        assert other.get("/api/backups/schedule").status_code == 403, role
        body = {"frequency": "daily"}
        assert other.put("/api/backups/schedule", json=body).status_code == 403, role
    assert "schedule" not in backup_service.read_backup_settings()
