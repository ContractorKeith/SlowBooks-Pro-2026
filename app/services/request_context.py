# ============================================================================
# Per-request context — who is acting.
#
# Set by the session middleware, read by the audit hooks (which live at
# the SQLAlchemy layer and have no access to the request). A contextvar
# survives async task switches, so concurrent Server Edition requests
# can't bleed identities into each other's audit rows.
# ============================================================================

import contextvars

acting_username: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "acting_username", default=None
)
