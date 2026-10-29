"""Restore from a backup file chosen on the person's computer (#651).

The file is copied into the backup folder and restored the ordinary way, so
it gets the same checks as a listed backup: another company's books need a
second confirmation, and a safety copy of the books as they are comes first.
Anything that isn't a company backup is refused and nothing is left behind.
"""

# ruff: noqa: F811 (the books fixture is imported, then named as an argument)

import io
import sqlite3
from contextlib import closing

from app.services import backup_service
from tests.test_backups_per_company import (  # noqa: F401 (fixture)
    _backup,
    _change,
    _make_books,
    _value,
    books,
)


def _head():
    return backup_service._migration_script()[1].get_current_head()


def _upload(client, name, data):
    return client.post(
        "/api/backups/upload",
        files={"file": (name, io.BytesIO(data), "application/octet-stream")},
    )


def _company_file(tmp_path, company, value):
    path = tmp_path / "from-a-thumb-drive.db"
    _make_books(path, company, value, revision=_head())
    return path.read_bytes()


def _left_behind(books):
    return sorted(p.name for p in books.backups.iterdir())


def test_a_backup_from_elsewhere_is_restored(books, client, tmp_path):
    data = _company_file(tmp_path, "Harbor Light Bakery", "from the drive")
    r = _upload(client, "harbor-light-bakery_20260101_120000.db", data)
    assert r.status_code == 200, r.text
    name = r.json()["filename"]
    assert name == "harbor-light-bakery_20260101_120000.db"
    assert (books.backups / name).read_bytes() == data
    assert name in {b["filename"] for b in client.get("/api/backups").json()}

    r = client.post("/api/backups/restore", json={"filename": name})
    assert r.status_code == 200, r.text
    assert _value(books.live) == "from the drive"
    assert _value(books.backups / r.json()["safety_backup"]) == "original"


def test_a_real_backup_downloaded_and_uploaded_again_restores(books, client):
    # the test books stand in for a migrated company file
    with closing(sqlite3.connect(books.live)) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
        conn.execute("INSERT INTO alembic_version VALUES (?)", (_head(),))
        conn.commit()
    name = _backup(client)
    data = client.get(f"/api/backups/download/{name}").content
    (books.backups / name).unlink()  # the copy on the thumb drive is all that is left

    _change(books.live, "changed since")
    r = _upload(client, name, data)
    assert r.status_code == 200, r.text
    assert r.json()["filename"] == name
    r = client.post("/api/backups/restore", json={"filename": name})
    assert r.status_code == 200, r.text
    assert _value(books.live) == "original"
    assert _value(books.backups / r.json()["safety_backup"]) == "changed since"


def test_it_goes_to_the_chosen_folder(books, client, tmp_path):
    chosen = tmp_path / "external"
    chosen.mkdir()
    client.put("/api/backups/folder", json={"folder": str(chosen)})
    data = _company_file(tmp_path, "Harbor Light Bakery", "v1")
    name = _upload(client, "from-drive.db", data).json()["filename"]
    assert (chosen / name).exists() and not (books.backups / name).exists()


def test_another_companys_file_still_needs_a_second_confirmation(
    books, client, tmp_path
):
    data = _company_file(tmp_path, "Riverbend Books", "theirs")
    name = _upload(client, "riverbend.db", data).json()["filename"]
    r = client.post("/api/backups/restore", json={"filename": name})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "other_company"
    assert _value(books.live) == "original"

    r = client.post(
        "/api/backups/restore", json={"filename": name, "allow_other_company": True}
    )
    assert r.status_code == 200, r.text
    assert _value(books.live) == "theirs"


def test_a_file_that_is_not_a_backup_is_refused(books, client, tmp_path):
    plain = tmp_path / "plain.db"
    with closing(sqlite3.connect(plain)) as conn:
        conn.execute("CREATE TABLE notes (v TEXT)")
        conn.commit()
    for name, data in (
        ("photo.db", b"\x89PNG\r\n\x1a\n" + b"0" * 100),
        ("empty.db", b""),
        ("plain.db", plain.read_bytes()),  # a database, not a company file
    ):
        r = _upload(client, name, data)
        assert r.status_code == 400, (name, r.text)
        assert isinstance(r.json()["detail"], str)
    assert _left_behind(books) == []


def test_a_file_over_the_cap_is_refused_and_removed(
    books, client, tmp_path, monkeypatch
):
    from app.services import upload_limits

    data = _company_file(tmp_path, "Harbor Light Bakery", "big")
    monkeypatch.setattr(upload_limits, "MAX_BACKUP_UPLOAD_BYTES", len(data) - 1)
    r = _upload(client, "big.db", data)
    assert r.status_code == 413, r.text
    assert _left_behind(books) == []


def test_an_unplugged_folder_refuses_the_upload(books, client, tmp_path):
    chosen = tmp_path / "drive"
    chosen.mkdir()
    client.put("/api/backups/folder", json={"folder": str(chosen)})
    chosen.rmdir()
    data = _company_file(tmp_path, "Harbor Light Bakery", "v1")
    r = _upload(client, "x.db", data)
    assert r.status_code == 500
    assert "not available" in r.json()["detail"]
    assert not chosen.exists()


def test_a_name_never_overwrites_or_escapes_the_folder(books, client, tmp_path):
    data = _company_file(tmp_path, "Harbor Light Bakery", "v1")
    first = _upload(client, "same.db", data).json()["filename"]
    second = _upload(client, "same.db", data).json()["filename"]
    third = _upload(client, "same.db", data).json()["filename"]
    assert (first, second, third) == ("same.db", "same-2.db", "same-3.db")

    for hostile in ("../../outside.db", "..\\..\\outside.db", "/etc/passwd.db"):
        r = _upload(client, hostile, data)
        assert r.status_code == 200, r.text
        assert (books.backups / r.json()["filename"]).parent == books.backups
    assert not (tmp_path / "outside.db").exists()

    long = _upload(client, "a" * 300 + ".db", data).json()["filename"]
    assert long == "a" * 100 + ".db"
    assert _upload(client, "we ird; $name!.db", data).json()["filename"] == (
        "we-ird-name.db"
    )


def test_an_upload_does_not_hide_an_older_backup_of_the_same_name(
    books, client, tmp_path
):
    chosen = tmp_path / "external"
    chosen.mkdir()
    data = _company_file(tmp_path, "Harbor Light Bakery", "v1")
    old = _upload(client, "same.db", data).json()["filename"]  # default folder
    client.put("/api/backups/folder", json={"folder": str(chosen)})
    new = _upload(client, "same.db", data).json()["filename"]
    assert (old, new) == ("same.db", "same-2.db")


# An encrypted company's backups have no SQLite header (#290): its own are
# taken, one under another key is refused, and a plain company takes neither.

KEY = bytes(range(32))


def _encrypted(books, monkeypatch, tmp_path, key=KEY):
    from app.services import company_file

    company_file.convert(books.live, KEY, encrypt=True)
    monkeypatch.setenv(company_file.KEY_ENV, KEY.hex())
    path = tmp_path / "encrypted-backup.db"
    _make_books(path, "Harbor Light Bakery", "from the drive", revision=_head())
    company_file.convert(path, key, encrypt=True)
    return path.read_bytes()


def test_an_encrypted_companys_own_backup_is_taken(
    books, client, tmp_path, monkeypatch
):
    data = _encrypted(books, monkeypatch, tmp_path)
    assert not data.startswith(b"SQLite format 3")
    r = _upload(client, "harbor-light-bakery_20260101_120000.db", data)
    assert r.status_code == 200, r.text
    assert (books.backups / r.json()["filename"]).read_bytes() == data


def test_a_backup_under_another_key_is_refused(books, client, tmp_path, monkeypatch):
    data = _encrypted(books, monkeypatch, tmp_path, key=bytes(range(1, 33)))
    r = _upload(client, "someone-elses.db", data)
    assert r.status_code == 400
    assert "key" in r.json()["detail"]
    assert _left_behind(books) == []


def test_a_file_named_like_an_automatic_backup_is_not_one(books, client, tmp_path):
    data = _company_file(tmp_path, "Harbor Light Bakery", "elsewhere")
    for tag in ("auto", "auto-2", "before-restore"):
        name = _upload(
            client, f"harbor-light-bakery_20260101_120000-{tag}.db", data
        ).json()["filename"]
        assert "-uploaded" in name and tag not in name
        assert not backup_service.is_auto_backup(name)


def test_an_upload_that_says_it_is_too_big_is_refused_up_front():
    from app.routes.backups import _declared_too_big
    from app.services.upload_limits import MAX_BACKUP_UPLOAD_BYTES

    assert _declared_too_big(str(MAX_BACKUP_UPLOAD_BYTES + 1))
    assert not _declared_too_big(str(MAX_BACKUP_UPLOAD_BYTES))
    assert not _declared_too_big(None) and not _declared_too_big("junk")


def test_a_spoofed_content_length_gets_a_413_before_the_body(books, client):
    from app.services.upload_limits import MAX_BACKUP_UPLOAD_BYTES

    r = client.post(
        "/api/backups/upload",
        files={"file": ("x.db", io.BytesIO(b"tiny"), "application/octet-stream")},
        headers={"Content-Length": str(MAX_BACKUP_UPLOAD_BYTES + 1)},
    )
    assert r.status_code == 413, r.text
    assert _left_behind(books) == []
