"""Python client for the Virginia Legislative Information System (LIS) REST API.

There are two entry points:

- :class:`LISService` is the one to reach for first.  It takes bill numbers
  and committee names, joins the reference vocabularies, and caches the large
  lists.
- :class:`LISClient` is transport: one method per endpoint, and no
  interpretation.

The docstring of each method records the traps of its endpoint.  The guides
that cross endpoints live in the repository, not in the installed package:
https://github.com/JonnyWaffles/va-lis-client/tree/main/docs
"""

from va_lis_client.client import LISClient
from va_lis_client.exceptions import (
    BillNotFoundError,
    CommitteeNotFoundError,
    InvalidBillNumberError,
    LISClientError,
    LISError,
    SessionNotFoundError,
    TextVersionNotFoundError,
)
from va_lis_client.service import (
    DEFAULT_BILL_LIST_TTL,
    DEFAULT_ROSTER_TTL,
    BillVote,
    CommitteeSeat,
    DocketEntry,
    LISService,
    MemberVote,
    MinutesRecord,
    RollCallEntry,
    normalize_bill_number,
    pick_text_version,
    strip_html,
)

__all__ = [
    "BillNotFoundError",
    "BillVote",
    "CommitteeNotFoundError",
    "CommitteeSeat",
    "DEFAULT_BILL_LIST_TTL",
    "DEFAULT_ROSTER_TTL",
    "DocketEntry",
    "InvalidBillNumberError",
    "LISClient",
    "LISClientError",
    "LISError",
    "LISService",
    "MemberVote",
    "MinutesRecord",
    "RollCallEntry",
    "SessionNotFoundError",
    "TextVersionNotFoundError",
    "normalize_bill_number",
    "pick_text_version",
    "strip_html",
]
