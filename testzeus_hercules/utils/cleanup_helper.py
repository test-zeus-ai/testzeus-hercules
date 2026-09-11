"""Helpers for aggregating resource-cleanup errors without losing cancellation.

Cleanup paths deliberately collect every failure so one broken component can
never skip the teardown of the others. Aggregation has one hazard though: a
cancelled run whose teardown is interrupted must keep behaving like a cancelled
run, not like a slow error report. :func:`raise_if_cancelled` restores that
precedence after the errors have been collected.
"""

import asyncio

from testzeus_hercules.utils.logger import logger


def raise_if_cancelled(errors: list[BaseException]) -> None:
    """Re-raise a CancelledError found in ``errors``, keeping the other errors.

    If any collected error is an ``asyncio.CancelledError``, it is re-raised
    as-is with the remaining errors attached as a ``BaseExceptionGroup`` on
    ``__cause__``, so cancellation is never swallowed by error aggregation and
    no collected detail is dropped either. Does nothing when no cancellation is
    present; raising the plain group is left to the caller.
    """
    for error in errors:
        if isinstance(error, asyncio.CancelledError):
            other_errors = [item for item in errors if item is not error]
            if other_errors:
                logger.error(
                    "Cancellation during cleanup; re-raising it with %d other cleanup error(s) attached.",
                    len(other_errors),
                )
                raise error from BaseExceptionGroup("Other resource cleanup failures", other_errors)
            raise error
