"""What a caught exception may say to the user.

A route or importer that catches a bare Exception must not put the
exception's own text in a response: SQLAlchemy errors carry the whole
statement and every bound parameter (a live payment token, in the case
macbase1 caught on the 2.9.4 gate), driver errors carry hostnames, and
tracebacks carry paths. The rule here: data problems in our own words
pass through; a database constraint is reduced to the constraint; anything
else is logged with its traceback and answered with one fixed sentence.
"""

import logging
from decimal import InvalidOperation

from sqlalchemy.exc import IntegrityError, StatementError

logger = logging.getLogger(__name__)

GENERIC = "unexpected error — the server log has the details"

# Exceptions whose message is ours: the importers and services raise
# ValueError with a sentence for the user ("Missing customer NAME"). Not
# LookupError / ArithmeticError — those are what Python raises when the
# code is wrong (KeyError, IndexError, ZeroDivisionError), and a bug must
# be logged, not handed to the user as "'ACCNT'" (macbase1, 2.9.4 gate).
DATA_ERRORS = (ValueError,)


def safe_message(exc: BaseException, context: str = "operation") -> str:
    """The sentence a user may see for `exc`. Call from inside the except
    block so the traceback is attached to the log line."""
    if isinstance(exc, IntegrityError):
        # sqlite3 / psycopg wrap the statement and parameters in str(exc);
        # the driver's own first line is the constraint, which is the
        # useful part ("NOT NULL constraint failed: invoices.invoice_number").
        orig = str(getattr(exc, "orig", "") or "").strip().splitlines()
        logger.warning("%s: database constraint", context, exc_info=True)
        return "Database constraint: " + (orig[0] if orig else "see the server log")
    if isinstance(exc, StatementError) and not isinstance(exc, DATA_ERRORS):
        logger.exception("%s: database error", context)
        return "Database error — the server log has the details"
    if isinstance(exc, InvalidOperation):
        # decimal's own text is "[<class 'decimal.ConversionSyntax'>]"
        logger.info("%s: a number could not be read", context)
        return "a number could not be read"
    if isinstance(exc, DATA_ERRORS):
        logger.info("%s: %s", context, exc)
        return str(exc)
    logger.exception("%s: unexpected error", context)
    return GENERIC
