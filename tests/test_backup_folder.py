"""The backup folder is chosen in Settings (#651).

New backups go to the chosen folder; backups already in the default folder
stay listed, downloadable and restorable. A folder that isn't there (a drive
that isn't plugged in) is refused when chosen and makes a backup fail with a
sentence, never a folder quietly created on the startup disk.
"""

# ruff: noqa: F811 (the books fixture is imported, then named as an argument)

import json

from app.services import backup_service
from tests.test_backups_per_company import (  # noqa: F401 (fixture)
    _backup,
    _change,
    _listed,
    _value,
    books,
)


def test_the_default_folder_until_one_is_chosen(books, client):
    r = client.get("/api/backups/folder")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "folder": str(books.backups),
        "default_folder": str(books.backups),
        "is_default": True,
        "available": True,
    }
    assert backup_service.custom_backup_dir() is None


def test_new_backups_go_to_the_chosen_folder(books, client, tmp_path):
    chosen = tmp_path / "external"
    chosen.mkdir()
    r = client.put("/api/backups/folder", json={"folder": str(chosen)})
    assert r.status_code == 200, r.text
    assert r.json()["is_default"] is False
    assert r.json()["folder"] == str(chosen.resolve())

    new = _backup(client)
    assert (chosen / new).exists() and not (books.backups / new).exists()
    assert set(_listed(client)) == {new}
    assert client.get(f"/api/backups/download/{new}").status_code == 200


def test_the_choice_lives_in_a_file_beside_the_default_backups(books, client, tmp_path):
    chosen = tmp_path / "external"
    chosen.mkdir()
    client.put("/api/backups/folder", json={"folder": str(chosen)})
    saved = json.loads((books.backups / "backup-settings.json").read_text())
    assert saved == {"folder": str(chosen.resolve())}
    assert not list(books.backups.glob("*.tmp"))  # written whole, then renamed


def test_older_backups_in_the_default_folder_still_list_and_restore(
    books, client, tmp_path
):
    old = _backup(client)
    chosen = tmp_path / "external"
    chosen.mkdir()
    client.put("/api/backups/folder", json={"folder": str(chosen)})
    new = _backup(client)
    assert set(_listed(client)) == {old, new}
    assert client.get(f"/api/backups/download/{old}").status_code == 200

    _change(books.live, "changed since")
    r = client.post("/api/backups/restore", json={"filename": old})
    assert r.status_code == 200, r.text
    assert _value(books.live) == "original"
    # the safety copy goes where new backups go
    assert (chosen / r.json()["safety_backup"]).exists()


def test_a_folder_that_cannot_be_used_is_refused(books, client, tmp_path):
    a_file = tmp_path / "a-file"
    a_file.write_text("x")
    for folder in (str(tmp_path / "missing"), "relative/path", str(a_file)):
        r = client.put("/api/backups/folder", json={"folder": folder})
        assert r.status_code == 400, (folder, r.text)
        assert isinstance(r.json()["detail"], str)
    assert not (tmp_path / "missing").exists()  # never created
    assert client.get("/api/backups/folder").json()["is_default"] is True


def test_a_folder_that_cannot_be_written_is_refused(
    books, client, tmp_path, monkeypatch
):
    def refuse(*args, **kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(backup_service.tempfile, "NamedTemporaryFile", refuse)
    r = client.put("/api/backups/folder", json={"folder": str(tmp_path)})
    assert r.status_code == 400, r.text
    assert "can't save files" in r.json()["detail"]


def test_a_root_folder_contains_what_is_in_it():
    import ntpath
    import posixpath

    assert backup_service._is_within("/", "/a.db", posixpath)
    assert backup_service._is_within("/mnt/x", "/mnt/x/a.db", posixpath)
    assert not backup_service._is_within("/mnt/x", "/mnt/xy/a.db", posixpath)
    assert backup_service._is_within("C:\\", "C:\\a.db", ntpath)
    assert backup_service._is_within("E:\\Backups", "e:\\backups\\a.db", ntpath)
    assert not backup_service._is_within("C:\\", "D:\\a.db", ntpath)  # other drive


def test_an_unplugged_folder_fails_the_backup_and_creates_nothing(
    books, client, tmp_path
):
    chosen = tmp_path / "drive"
    chosen.mkdir()
    client.put("/api/backups/folder", json={"folder": str(chosen)})
    chosen.rmdir()

    assert client.get("/api/backups/folder").json()["available"] is False
    r = client.post("/api/backups")
    assert r.status_code == 500
    assert "not available" in r.json()["detail"]
    assert not chosen.exists()
    assert [p.name for p in books.backups.iterdir()] == ["backup-settings.json"]


def test_blank_goes_back_to_the_default(books, client, tmp_path):
    chosen = tmp_path / "external"
    chosen.mkdir()
    client.put("/api/backups/folder", json={"folder": str(chosen)})
    r = client.put("/api/backups/folder", json={"folder": "  "})
    assert r.status_code == 200, r.text
    assert r.json()["is_default"] is True
    assert (books.backups / _backup(client)).exists()


def test_only_an_administrator_reads_the_folder(books, client):
    from tests.test_backup_download_admin import _as

    reader = _as(client, "readonly")
    assert reader.get("/api/backups/folder").status_code == 403
    assert reader.put("/api/backups/folder", json={"folder": ""}).status_code == 403
