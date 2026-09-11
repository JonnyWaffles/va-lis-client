"""Exception hierarchy for the package.

Every error this package raises derives from :class:`LISError`, so a caller
can map the whole package onto one error response with a single ``except``.
The resolution errors also derive from the builtin that best describes them
(``ValueError``, ``LookupError``), so code that catches the builtin keeps
working.
"""


class LISError(Exception):
    """Base class for every error this package raises."""


class LISClientError(LISError):
    """Raised when the LIS API returns a non-success response."""


class InvalidBillNumberError(LISError, ValueError):
    """Raised when a string does not parse as a bill number."""


class BillNotFoundError(LISError, LookupError):
    """Raised when a bill number does not appear in a session's bill list."""


class TextVersionNotFoundError(LISError, LookupError):
    """Raised when a bill has no text version matching the requested code."""


class SessionNotFoundError(LISError, LookupError):
    """Raised when a session code does not appear in the session reference list."""
