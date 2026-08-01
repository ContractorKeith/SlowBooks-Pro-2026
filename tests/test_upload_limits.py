"""Upload size limits on the import endpoints.

Import routes previously buffered `await file.read()` unbounded; every
one now reads through upload_limits.read_limited() and rejects oversize
bodies with 413 before parsing anything.
"""

import io

from app.services.upload_limits import MAX_IMPORT_BYTES


def _big_file(size: int):
    return io.BytesIO(b"x" * size)


def test_iif_import_rejects_oversize(client):
    resp = client.post(
        "/api/iif/import",
        files={"file": ("huge.iif", _big_file(MAX_IMPORT_BYTES + 1), "text/plain")},
    )
    assert resp.status_code == 413
    assert "too large" in resp.json()["detail"].lower()


def test_bank_import_preview_rejects_oversize(client):
    resp = client.post(
        "/api/bank-import/preview",
        files={"file": ("huge.ofx", _big_file(MAX_IMPORT_BYTES + 1), "text/plain")},
    )
    assert resp.status_code == 413


def test_csv_import_rejects_oversize(client):
    resp = client.post(
        "/api/csv/import/customers",
        files={"file": ("huge.csv", _big_file(MAX_IMPORT_BYTES + 1), "text/csv")},
    )
    assert resp.status_code == 413


def test_normal_size_still_accepted(client):
    """A normal small upload passes the limiter (and fails later for its
    own domain reasons, not 413)."""
    resp = client.post(
        "/api/csv/import/customers",
        files={"file": ("ok.csv", io.BytesIO(b"name\nAcme\n"), "text/csv")},
    )
    assert resp.status_code != 413
