"""Advanced legislation search — result rows and the reference vocabularies.

These back ``/AdvancedLegislationSearch/api/``.  The search is the only
keyword-capable bill endpoint in LIS, and the only one that filters on status,
category, committee, chapter number, and patron in a single call.

Two things about :class:`LegislationSearchResult` shape every caller:

- **A bill can arrive more than once.**  The endpoint returns one row per
  published summary version, and it also emits genuine duplicate rows.  See
  :data:`SUMMARY_VERSION_RANK` and ``LISService.search_bills``.
- **Seven fields are always null**, including every date.  The row carries
  ``LegislationStatus`` as a display string but never ``LegislationStatusID``,
  so joining it to the status vocabulary takes the name.
"""

from datetime import datetime

from va_lis_client.models.common import LISModel
from va_lis_client.models.legislation import Patron

# Progression order of the ``SummaryVersion`` labels, lowest first.  A bill
# publishes a new summary as it advances, and several endpoints return every
# one of them as its own row, so collapsing a bill to one row means picking a
# winner.
#
# **Two services spell the chamber passage summary differently**, and neither
# ever sends the other's spelling (session 20261, measured 2026-09-15):
#
# - ``/AdvancedLegislationSearch`` sends ``SUMMARY AS PASSED HOUSE`` and
#   ``SUMMARY AS PASSED SENATE``.  All five of its labels are below.
# - ``/LegislationByMember`` sends ``SUMMARY AS PASSED CHAMBER`` instead, and
#   sends only four labels: it has no per-chamber spelling at all.
#
# A bill never carries two of the rank-1 spellings at once, so they share a
# rank.  Any consumer of either service must use this whole table.
#
# **LIS's own numbering agrees with these ranks.**  A third service,
# ``/LegislationText``'s ``getlegislationsummariesasync``, sends a numeric
# ``SummaryVersionID`` alongside the label, and it lines up exactly: 1 is
# introduced, 2 is either chamber's passage, 3 is final passage (checked on
# HB1, HB18, HB220, HB1115, HB1503 and SB605 in 20261, 2026-09-15).  Neither
# the search row nor the member row carries that ID, which is why this table
# has to work from the label.  Note that endpoint returns its rows newest
# first, the reverse of the other two.
#
# **Do not order these rows by arrival.**  LIS usually sends them in
# progression order, but not always, and the same bill breaks it on both
# services.  HB1503 and SB605 arrived from the search with ``SUMMARY AS
# PASSED`` *before* the chamber passage row (2 of its 52 multi-version
# bills), and HB1503 did the same on the member list (1 of 57 groups across
# a 40-member sample).  Rank the label instead.
SUMMARY_VERSION_RANK: dict[str, int] = {
    "SUMMARY AS INTRODUCED": 0,
    "SUMMARY AS PASSED HOUSE": 1,  # search only
    "SUMMARY AS PASSED SENATE": 1,  # search only
    "SUMMARY AS PASSED CHAMBER": 1,  # /LegislationByMember only
    "SUMMARY AS PASSED": 2,
    "SUMMARY AS ENACTED WITH GOVERNOR'S RECOMMENDATION": 3,
}

# Rank for a label LIS adds that this table does not list yet.  It sorts below
# every known label on purpose: a stale-but-verified summary beats guessing
# that an unrecognized label means "newest".  Add the label above when one
# appears.
UNKNOWN_SUMMARY_RANK = -1


def summary_version_rank(label: str | None) -> int:
    """How far along a ``SummaryVersion`` label sits, for picking a winner.

    Returns :data:`UNKNOWN_SUMMARY_RANK` for a label that
    :data:`SUMMARY_VERSION_RANK` does not carry, which sorts it below every
    known label.
    """
    if label is None:
        return UNKNOWN_SUMMARY_RANK

    return SUMMARY_VERSION_RANK.get(label.upper(), UNKNOWN_SUMMARY_RANK)


class SearchTextMatch(LISModel):
    """A keyword hit inside a bill's text, nested on a search result.

    Present only on a search that sends ``KeywordExpression``; a filter-only
    search returns ``SearchText: []``.  Even on a keyword search the observed
    rows carry ``countMatches: 0`` with null ``Description`` and
    ``DocumentCode``, so treat a populated list as "this row matched" and
    nothing finer.

    Example::

        {"LegislationTextID": 0, "Description": null,
         "DocumentCode": null, "countMatches": 0}
    """

    LegislationTextID: int | None = None
    Description: str | None = None
    DocumentCode: str | None = None
    countMatches: int | None = None


class LegislationSearchResult(LISModel):
    """One row of ``/AdvancedLegislationSearch/api/getlegislationlistasync``.

    Richer than the session bill list: it carries the summary HTML, the
    summary version, the chapter number, and the committee of reference.

    **It does not carry co-patrons.**  ``Patrons`` holds exactly one entry,
    the chief patron, on all 3,007 rows of session 20261 — the same as the
    session bill list.  Co-patronage is reachable only through
    ``/LegislationPatron`` or the member-first list.

    **The row is not one bill.**  ``LegislationID`` repeats for two unrelated
    reasons, and both appeared in session 20261 (measured 2026-09-15, 3,007
    rows over 2,827 unique IDs):

    - **One row per summary version.**  52 bills arrived 2 or 3 times, once
      per ``SummaryVersion``.  This is structural.  Rank the label with
      :func:`summary_version_rank` to pick the newest; see
      :data:`SUMMARY_VERSION_RANK` for why arrival order will not do.
    - **Exact duplicate rows.**  123 bills arrived twice with every field
      byte-identical, so nothing in the response tells the copies apart.
      121 of the 123 held status ``Continued`` and the other two were HJ29
      and HJ30 (``Passed``).  It is not every continued bill: 444 carry that
      status and only 121 doubled.  Treat these as bad rows in LIS.

    ``LISService.search_bills`` and ``LISService.search_bill_summaries``
    resolve this; the client hands the rows back exactly as LIS sent them.

    **Fields LIS never populates here**, even though the schema declares
    them: ``CandidateDate``, ``VersionDate``, ``IntroductionDate``,
    ``HousePassageDate``, ``SenatePassageDate``, ``Sessions``, and
    ``LegislationStatusID``.  All 3,007 rows were null on every one.  So the
    row gives no date at all, and no carry-over lineage — fetch those from
    ``/Legislation``.  ``LegislationTextID`` is present but always ``0``.

    ``CommitteeID`` is ``0``, not null, when no committee holds the bill.
    ``SessionCode`` arrives as the string ``"20261"`` here and as an integer
    on other services; it is typed ``int`` so both shapes land the same way.

    Example::

        {"LegislationID": 98525, "LegislationNumber": "HB1",
         "FullNumber": "HB0001", "ChamberCode": "H",
         "LegislationStatus": "Acts of Assembly Chapter",
         "SummaryVersion": "SUMMARY AS PASSED", "ChapterNumber": "CHAP0350",
         "Description": "Minimum wage; increases incrementally...",
         "Patrons": [{"MemberDisplayName": "Jeion A. Ward", ...}]}
    """

    LegislationID: int  # surrogate PK — repeats, see the docstring
    LegislationNumber: str  # e.g. "HB1" (unpadded)
    FullNumber: str | None = None  # zero-padded, e.g. "HB0001"
    LegislationKey: int | None = None  # numeric part of the bill number
    ChamberCode: str | None = None  # "H" or "S"
    LegislationTypeCode: str | None = None  # "B"=Bill, "J"=Joint Resolution, "R"=Resolution
    LegislationClass: str | None = None  # e.g. "Legislation", "Commending Resolution"
    LegislationClassID: int | None = None

    Description: str | None = None  # short description
    LegislationTitle: str | None = None  # full formal title
    LegislationSummary: str | None = None  # summary HTML — pass to strip_html
    SummaryVersion: str | None = None  # see SUMMARY_VERSION_RANK
    SearchText: list[SearchTextMatch] = []  # keyword hits; empty unless you sent a keyword

    LegislationStatus: str | None = None  # display name; join on the name, the ID is null
    LegislationStatusID: int | None = None  # always null here
    ChapterNumber: str | None = None  # e.g. "CHAP0350", set once a bill is enacted
    EffectiveType: str | None = None
    EffectiveTypeID: int | None = None
    IsComplete: bool | None = None  # False on every observed row
    PendingChange: bool | None = None  # False on every observed row

    CommitteeID: int | None = None  # 0, not null, when no committee holds the bill
    CommitteeName: str | None = None
    CommitteeNumber: str | None = None  # e.g. "H08"
    ParentCommitteeName: str | None = None  # set on a subcommittee

    Patrons: list[Patron] = []  # the chief patron only, never co-patrons
    SessionID: int | None = None
    SessionCode: int | None = None  # LIS sends the string "20261" here
    SessionName: str | None = None

    LegislationTextID: int | None = None  # always 0 here
    CandidateDate: datetime | None = None  # always null here
    VersionDate: datetime | None = None  # always null here
    IntroductionDate: datetime | None = None  # always null here
    HousePassageDate: datetime | None = None  # always null here
    SenatePassageDate: datetime | None = None  # always null here
    Sessions: list = []  # always empty here — no carry-over lineage

    @property
    def summary_rank(self) -> int:
        """This row's place in the summary progression, lowest first."""
        return summary_version_rank(self.SummaryVersion)


class LegislationCategory(LISModel):
    """A row of ``getlegislationcategoryreferencesasync``.

    30 rows, the vocabulary behind the search's ``LegislationCategoryID``.
    These name a stage a bill can sit at, e.g. ``"Introduced"``.

    Example::

        {"LegislationCategoryID": 1, "Name": "Introduced"}
    """

    LegislationCategoryID: int
    Name: str | None = None


class LegislationNumberEntry(LISModel):
    """A row of ``getlegislativenumbersasync`` — bill number to ID.

    The cheapest number-to-ID map in LIS: 3,646 rows and 268 KB for session
    20261, against roughly 3 MB for the session bill list.  Use it when you
    need to resolve numbers in bulk and want nothing else about the bill.

    Example::

        {"LegislationNumber": "HB1", "LegislationID": 98525,
         "LegislationKey": 1}
    """

    LegislationNumber: str
    LegislationID: int
    LegislationKey: int | None = None


class IntroductionDate(LISModel):
    """A row of ``getintroductiondatelistasync``.

    The distinct dates on which bills were introduced in a session, 91 rows
    for 20261.  The list carries dates and nothing else.

    Example::

        {"IntroductionDate": "2025-11-17T00:00:00"}
    """

    IntroductionDate: datetime | None = None
