"""Legislation text and summary models."""

from datetime import datetime

from va_lis_client.models.common import LISModel
from va_lis_client.models.legislation import Patron


class TextFile(LISModel):
    """A file attachment (PDF, HTML, impact statement, etc.) on a text version.

    ``TextFormatID`` / ``TextFormat`` indicate the type.  ``FileURL`` is
    a relative or absolute URL for retrieval.

    Example::

        {"LegislationTextID": 257719, "TextFormatID": 1,
         "TextFormat": "PDF", "FileURL": "/some/path.pdf",
         "PageCount": 3}
    """

    LegislationTextID: int
    TextFormatID: int
    TextFormat: str | None = None
    FileURL: str | None = None
    ReferenceNumber: str | None = None
    SessionID: int | None = None
    Description: str | None = None
    PageCount: int | None = None  # PDF only


class LegislationTextItem(LISModel):
    """A text version record from ``/LegislationText/api/getlegislationtextlistasync``.

    Query by ``legislation_number`` + ``session_code`` (e.g. ``HB1`` + ``20261``).
    Returns one row per version of the bill (introduced, enrolled, etc.).

    Key: ``LegislationTextID`` is the surrogate PK for a specific text version.
    ``DocumentCode`` is the human-readable version key (bill number + suffix):
    ``"HB1"`` (introduced), ``"HB1ER"`` (enrolled), ``"HB1S1"`` (substitute 1).

    Example::

        {"LegislationID": 98525, "LegislationNumber": "HB1",
         "Description": "Introduced", "SessionID": 59,
         "LegislationTextID": 257719, "DocumentCode": "HB1",
         "LDNumber": "26101997D", "LegislationVersionID": 1,
         "Version": "Introduced", "IsPublic": true, "IsComplete": true}
    """

    LegislationID: int  # FK to Legislation
    LegislationNumber: str  # e.g. "HB1"
    Description: str | None = None  # version name, e.g. "Introduced", "Enrolled"
    SessionID: int  # FK to Session
    LegislationTextID: int  # surrogate PK for this text version
    DraftDate: datetime | None = None
    DocumentCode: str | None = None  # bill number + version suffix, e.g. "HB1", "HB1ER"
    LDNumber: str | None = None  # legislative drafting number, e.g. "26101997D"
    LegislationVersionID: int  # FK to LegislationVersion (1=Intro, 3=Enrolled)
    Version: str | None = None  # display name, e.g. "Introduced"
    IsPublic: bool = True
    IsComplete: bool | None = False  # True when this version is finalized
    GovernorsRequest: bool | None = False  # LIS returns null on many bills
    IsEmergency: bool | None = False
    IsReprint: bool | None = False
    TextDispositionID: int | None = None
    PDFFile: list[TextFile] | None = None
    HTMLFile: list[TextFile] | None = None
    ImpactFile: list[TextFile] | None = None  # fiscal impact statements
    LinkFile: list[TextFile] | None = None


class LegislationTextDetail(LISModel):
    """Full text detail from ``/LegislationText/api/getlegislationtextbyidasync``.

    Query by ``legislation_id`` + ``session_code``.  Returns one row per
    version, including the full bill text HTML in ``DraftText``.

    ``DraftText`` contains the bill body as HTML with inline styling classes.
    Additions are marked with ``<em class="new">`` and deletions with ``<s>``.

    Example (truncated)::

        {"LegislationTextID": 266240, "LegislationVersionID": 3,
         "LegislationVersion": "Enrolled", "LegislationID": 98525,
         "LegislationNumber": "HB1", "DocumentCode": "HB1ER",
         "SessionCode": "20261", "ChamberCode": "H",
         "DraftText": "<p class=\\"ldtitle\\">An Act to amend ..."}
    """

    LegislationTextID: int  # surrogate PK
    LegislationVersionID: int  # FK to LegislationVersion
    LegislationVersion: str | None = None  # e.g. "Enrolled", "Introduced"
    LegislationID: int  # FK to Legislation
    LegislationNumber: str  # e.g. "HB1"
    ChamberCode: str | None = None  # "H" or "S"
    LegislationChamberCode: str | None = None  # same as ChamberCode
    SessionCode: str | None = None  # e.g. "20261"
    SessionID: int | None = None
    LegislationTextActionID: int | None = None
    DocumentCode: str  # e.g. "HB1ER"
    DraftText: str | None = None  # full bill text as HTML
    DraftTitle: str | None = None
    LDNumber: str | None = None  # e.g. "26101997D"
    VersionDate: datetime | None = None
    VersionCode: str | None = None  # suffix, e.g. "ER", "S1"
    EventCode: str | None = None
    DocURL: str | None = None
    LinkURL: str | None = None
    Description: str | None = None
    Sponsor: str | None = None
    SponsorTypeID: int | None = None
    SessionYear: int | None = None
    IsPublic: bool = True
    IsActive: bool = True
    IsComplete: bool | None = False
    GovernorsRequest: bool | None = False
    IsEmergency: bool | None = False
    IsReprint: bool | None = False
    TextDispositionID: int | None = None
    TextDisposition: str | None = None
    CommitteeID: int | None = None
    CommitteeName: str | None = None
    LegislationClassID: int | None = None
    LegislationClass: str | None = None  # "Legislation"
    Patrons: list[Patron] | None = None
    PDFFile: list[TextFile] | None = None
    HTMLFile: list[TextFile] | None = None
    ImpactFile: list[TextFile] | None = None
    LinkFile: list[TextFile] | None = None
    JSONFile: list[TextFile] | None = None


class LegislationSummary(LISModel):
    """Bill summary from ``/LegislationSummary/api/getlegislationsummarylistasync``.

    Query by ``legislation_number`` + ``session_code`` (e.g. ``HB1`` + ``20261``).
    Returns one row per summary version (introduced, passed, etc.).

    ``Summary`` contains HTML with a ``<p class="sumtext">`` wrapper.
    ``SummaryVersion`` is a label like ``"SUMMARY AS INTRODUCED"`` or
    ``"SUMMARY AS PASSED"``.  The active summary (``IsActive=True``) is
    the current/latest one.

    Example::

        {"LegislationID": 98525, "SessionID": 59,
         "LegislationSummaryID": 134212,
         "SummaryVersion": "SUMMARY AS PASSED",
         "LegislationNumber": "HB1",
         "Summary": "<p class=\\"sumtext\\"><b>Minimum wage.</b> Increases...",
         "DocumentCode": "HB1SER", "SummaryVersionID": 3,
         "IsPublic": true, "IsActive": true}
    """

    LegislationID: int  # FK to Legislation
    SessionID: int  # FK to Session
    LegislationSummaryID: int  # surrogate PK
    SummaryVersion: str | None = None  # e.g. "SUMMARY AS PASSED"
    LegislationNumber: str  # e.g. "HB1"
    Summary: str | None = None  # HTML content
    DocumentCode: str | None = None  # e.g. "HB1SER" (summary doc code)
    LDNumber: str | None = None  # legislative drafting number
    SummaryDate: datetime | None = None
    SummaryVersionID: int | None = None  # 1=Introduced, 3=Enrolled/Passed
    IsPublic: bool = True
    IsActive: bool = True  # True for the current/latest summary


class SummaryVersion(LISModel):
    """A summary stage, from ``/LegislationSummary/api/getsummaryversionsreferenceasync``.

    Key: ``SummaryVersionID``, which is ``LegislationSummary.SummaryVersionID``.

    Four rows, and the id is the progression order:

    ==  ================================================  ====
    ID  Name                                              Code
    ==  ================================================  ====
    1   SUMMARY AS INTRODUCED                             S
    2   SUMMARY AS PASSED CHAMBER                         SE
    3   SUMMARY AS PASSED                                 SER
    4   SUMMARY AS ENACTED WITH GOVERNOR'S RECOMMENDATION SCH
    ==  ================================================  ====

    This is the official order behind
    :data:`~va_lis_client.models.search.SUMMARY_VERSION_RANK`.  It names the
    chamber stage ``PASSED CHAMBER`` only; the ``PASSED HOUSE`` and ``PASSED
    SENATE`` spellings that the advanced search sends are not in it.

    ``VersionCode`` is the suffix on a summary's ``DocumentCode``: HB1's
    passed summary is ``HB1SER``.
    """

    SummaryVersionID: int
    Name: str | None = None
    VersionCode: str | None = None


class TextDisposition(LISModel):
    """What became of a text, from ``gettextdispositionsreferenceasync``.

    Key: ``TextDispositionID``, carried by text rows, bill versions and
    amendments.

    13 rows, and the ids skip 9: 1 ``Offered``, 2 ``Recommended``, 3
    ``Reported``, 4 ``Rejected by Senate``, 5 ``Not Adopted``, 6 ``Adopted``,
    7 ``Adopted In-Part``, 8 ``Hold``, 10 ``Rejected by House``, 11
    ``Posted``, 12 ``Engrossed``, 13 ``Removed``, 14 ``Receded``.
    """

    TextDispositionID: int
    Name: str | None = None


class TextAction(LISModel):
    """An action that produces a text, from ``getlegislationtextactionlistasync``.

    Key: ``LegislationTextActionID``, which is
    ``LegislationTextDetail.LegislationTextActionID``.

    31 rows.  **Most ``EventCode`` values have no chamber prefix here.**  17
    of the 31 are four bare digits: the row says ``"4020"`` where a bill
    event says ``"H4020"`` or ``"S4020"``, so prepend the chamber before you
    join one to an event.  The other 14 are complete as sent, 2 with ``G``
    for the Governor (``"G9998"``) and 12 with ``C`` for a conference
    committee (``"C6038"``).

    Example::

        {"LegislationTextActionID": 2,
         "Description": "Prefiled and ordered printed", "EventCode": "4020"}
    """

    LegislationTextActionID: int
    Description: str | None = None
    EventCode: str | None = None  # "4020" with no chamber letter, or "G9998", "C6038"


class SponsorType(LISModel):
    """Who offered a text, from ``/LegislationText/api/getsponsortypelistasync``.

    Key: ``SponsorTypeID``, which is ``LegislationTextDetail.SponsorTypeID``.

    Six rows, sent in name order and not id order: 1 ``Governor``, 2
    ``Conference``, 3 ``Committee``, 4 ``Member``, 5 ``House``, 6 ``Senate``.
    """

    SponsorTypeID: int
    Name: str | None = None


class TextHitCount(LISModel):
    """How often a keyword occurs in one text.

    From ``/LegislationText/api/getlegislationtextshitcountsasync``.

    Key: ``LegislationTextID``.

    ``"wage"`` counted 11 in HB1's introduced text and 12 in each of its
    enrolled and chaptered texts (measured 2026-10-12).  An expression is a
    sum and not an intersection: ``"minimum AND wage"`` counted 23 in the
    introduced text, more than either word can reach alone.

    Example::

        {"LegislationTextID": 257719, "HitCount": 11}
    """

    LegislationTextID: int
    HitCount: int = 0
