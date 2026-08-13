"""Server Edition principal model (PR-S2): users table, legacy backfill,
single-user password-only login preserved, username login at 2+ users.

The `client` fixture arrives already set up (password "test-password-123")
and authenticated — which now also means the admin user row exists.
"""

from app.models.users import ROLE_ADMIN, ROLE_BOOKKEEPER, User
from app.services import auth as auth_service
from app.services.settings_service import get_setting_raw

FIXTURE_PW = "test-password-123"


def _mk_user(db, username, password, role=ROLE_BOOKKEEPER, active=True, name=""):
    u = User(
        username=username,
        display_name=name or username.title(),
        password_hash=auth_service.hash_password(password),
        role=role,
        is_active=active,
    )
    db.add(u)
    db.commit()
    return u


# ---------------------------------------------------------------------------
# Setup + backfill
# ---------------------------------------------------------------------------


def test_setup_creates_admin_user_row(client, db_session):
    users = db_session.query(User).all()
    assert len(users) == 1
    assert users[0].username == "admin"
    assert users[0].role == ROLE_ADMIN
    assert users[0].is_active
    # Same hash both places: the user row is the settings hash, verbatim
    assert users[0].password_hash == get_setting_raw(db_session, "auth_password_hash")


def test_legacy_install_backfills_on_first_login(client, db_session):
    # Simulate a pre-Server-Edition install: settings hash present, user
    # rows absent (they didn't exist before this feature).
    db_session.query(User).delete()
    db_session.commit()
    client.post("/api/auth/logout")

    r = client.post("/api/auth/login", json={"password": FIXTURE_PW})
    assert r.status_code == 200
    admin = db_session.query(User).one()
    assert admin.username == "admin"
    assert admin.role == ROLE_ADMIN
    assert admin.last_login_at is not None


# ---------------------------------------------------------------------------
# Single-user flow: identical UX to today
# ---------------------------------------------------------------------------


def test_single_user_login_ignores_username(client):
    client.post("/api/auth/logout")
    # Password-only works; a stray username is harmless
    r = client.post(
        "/api/auth/login",
        json={"password": FIXTURE_PW, "username": "whatever"},
    )
    assert r.status_code == 200
    status = client.get("/api/auth/status").json()
    assert status["multi_user"] is False
    assert status["user"]["username"] == "admin"
    assert status["user"]["role"] == ROLE_ADMIN


def test_set_password_keeps_admin_row_in_sync(client, db_session):
    auth_service.set_password(db_session, "new-password-22")
    client.post("/api/auth/logout")
    r = client.post("/api/auth/login", json={"password": "new-password-22"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Multi-user flow
# ---------------------------------------------------------------------------


def _add_renita_and_logout(client, db_session):
    _mk_user(db_session, "renita", "renita-password-1", name="Renita")
    client.post("/api/auth/logout")


def test_multi_user_requires_username(client, db_session):
    _add_renita_and_logout(client, db_session)
    status = client.get("/api/auth/status").json()
    assert status["multi_user"] is True

    r = client.post("/api/auth/login", json={"password": FIXTURE_PW})
    assert r.status_code == 400  # username now required

    r = client.post(
        "/api/auth/login", json={"username": "admin", "password": FIXTURE_PW}
    )
    assert r.status_code == 200
    assert client.get("/api/auth/status").json()["user"]["username"] == "admin"


def test_multi_user_second_account_and_role_in_session(client, db_session):
    _add_renita_and_logout(client, db_session)
    r = client.post(
        "/api/auth/login",
        json={"username": "RENITA", "password": "renita-password-1"},
    )
    # Case-insensitive username lookup
    assert r.status_code == 200
    user = client.get("/api/auth/status").json()["user"]
    assert user["username"] == "renita"
    assert user["display_name"] == "Renita"
    assert user["role"] == ROLE_BOOKKEEPER


def test_multi_user_rejects_wrong_password_and_unknown_user(client, db_session):
    _add_renita_and_logout(client, db_session)
    r = client.post(
        "/api/auth/login", json={"username": "renita", "password": "wrong-password"}
    )
    assert r.status_code == 401
    r = client.post(
        "/api/auth/login", json={"username": "ghost", "password": "renita-password-1"}
    )
    assert r.status_code == 401
    # Error message must not reveal which part was wrong
    assert "username or password" in r.json()["detail"].lower()


def test_inactive_user_cannot_log_in(client, db_session):
    _add_renita_and_logout(client, db_session)
    _mk_user(db_session, "gone", "gone-password-11", active=False)
    r = client.post(
        "/api/auth/login", json={"username": "gone", "password": "gone-password-11"}
    )
    assert r.status_code == 401


def test_cross_user_password_rejected(client, db_session):
    """renita's password must not unlock admin's account."""
    _add_renita_and_logout(client, db_session)
    r = client.post(
        "/api/auth/login", json={"username": "admin", "password": "renita-password-1"}
    )
    assert r.status_code == 401
