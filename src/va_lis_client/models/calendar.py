"""Calendar models (House floor agendas + both chambers)."""

from datetime import datetime

from pydantic import Field

from va_lis_client.models.common import LISModel
from va_lis_client.models.legislation import Patron
from va_lis_client.models.vote import VoteMember

__all__ = [
    "Agenda",
    "AgendaItem",
    "CalendarCategory",
    "CalendarCategoryType",
    "CalendarComment",
    "CalendarDetail",
    "CalendarFile",
    "CalendarItem",
    "CalendarType",
    "Staff",
    "VoteMember",
]


class CalendarFile(LISModel):
    """A file attachment on a calendar or docket (PDF, JSON).

    Example::

        {"CalendarFileID": 1211452, "CalendarID": 21146,
         "FileURL": "https://lis.blob.core.windows.net/files/1211452.PDF",
         "TextFormatID": 1, "IsPublic": true, "IsActive": true}
    """

    CalendarFileID: int
    CalendarID: int
    FileURL: str | None = None
    TextFormatID: int | None = None  # 1=PDF, 5=JSON
    IsGenerated: bool = False
    IsPublic: bool = True
    IsActive: bool = True
    ModificationDate: datetime | None = None


class CalendarComment(LISModel):
    """A comment/note on a calendar entry.

    Example::

        {"CalendarCommentID": 123, "CalendarID": 21146,
         "Comment": "Session will reconvene at 4pm", "Sequence": 1}
    """

    CalendarCommentID: int
    CalendarID: int
    Comment: str | None = None
    Sequence: int | None = None
    DeletionDate: datetime | None = None


class CalendarItem(LISModel):
    """A calendar list entry from ``/Calendar/api/getcalendarlistasync``.

    These are floor calendars (House and Senate).  For full detail with
    agendas and votes, use ``getcalendarsbyidasync``.  The 2026 House list
    holds 56, every one of type ``"Chamber"``.

    Key: ``CalendarID``.  ``ReferenceNumber`` is a human-readable code
    like ``"HC10314"`` (House Calendar 1, March 14); ``"HC20114"`` is the
    second calendar of January 14.

    Example::

        {"CalendarID": 21146, "ReferenceNumber": "HC10314",
         "CalendarDate": "2026-03-14T10:00:00",
         "CalendarType": "Chamber", "ChamberCode": "H",
         "SessionCode": "20261",
         "CalendarFiles": [{"FileURL": "...1211452.PDF", ...}]}
    """

    CalendarID: int  # surrogate PK
    ReferenceNumber: str | None = None  # e.g. "HC10314"
    SessionCode: str | None = None
    CalendarDate: datetime | None = None
    MeetingTime: str | None = None
    CalendarNumber: int | None = None
    Description: str | None = None
    IsPublic: bool = True
    CalendarType: str | None = None  # "Chamber", "Committee"
    CalendarTypeID: int | None = None
    ChamberCode: str | None = None  # "H" or "S"
    SessionID: int | None = None
    VoteRoomID: int | None = None
    RoomDescription: str | None = None
    Comments: str | None = None
    PendingChange: bool = False
    IsProforma: bool = False
    DeletionDate: datetime | None = None
    CalendarFiles: list[CalendarFile] = []
    CalendarComments: list[CalendarComment] = []


class AgendaItem(LISModel):
    """A sub-item on an agenda entry (action taken, vote result, etc.).

    Example::

        {"AgendaItemId": 66041, "AgendaId": 298539,
         "Description": "<p>Reported from Committee... March 13, 2026</p>",
         "VoteID": 302080,
         "VoteMember": [{"ResponseCode": "Y", ...}]}
    """

    AgendaItemId: int | None = None
    AgendaId: int | None = None
    Description: str | None = None  # HTML
    CalendarDescription: str | None = None
    LDTitle: str | None = None
    LegislationTextId: int | None = None
    LDNumber: str | None = None
    DraftText: str | None = None
    VoteID: int | None = None
    LegislationEventID: int | None = None
    IsActive: bool = True
    Sequence: int | None = None
    vote_members: list[VoteMember] = Field(default=[], alias="VoteMember")


class Agenda(LISModel):
    """An agenda entry on a calendar — typically one bill or action.

    Nested inside ``CalendarCategory.Agendas`` in calendar detail.

    Example::

        {"AgendaID": 298539, "LegislationID": 101934,
         "LegislationNumber": "SJ209",
         "LegislationDescription": "Governor; confirming appointments.",
         "Patrons": [...], "AgendaItems": [{...vote tallies...}]}
    """

    AgendaID: int | None = None
    Sequence: int | None = None
    Ranking: int | None = None
    CalendarCategoryID: int | None = None
    Description: str | None = None
    LegislationID: int | None = None
    LegislationKey: int | None = None
    LegislationNumber: str | None = None
    LegislationDescription: str | None = None
    LegislationTitle: str | None = None
    LDNumber: str | None = None
    Summary: str | None = None
    DraftTitle: str | None = None
    IsActive: bool = True
    IsHidden: bool | None = None
    CalendarCategoryTemplateID: int | None = None
    CommunicationID: int | None = None
    PageNumber: int | None = None
    EffectiveType: str | None = None
    CommitteeID: int | None = None
    CandidateDate: datetime | None = None
    DisplayType: bool | None = None
    Patrons: list[Patron] = []
    AgendaItems: list[AgendaItem] = []


class CalendarCategory(LISModel):
    """A section within a calendar (e.g. "Order of Business", "Senate Bills").

    Example::

        {"CalendarCategoryID": 11445, "CategoryCode": "Order",
         "Description": "Order of Business",
         "IsLegislationCategory": false,
         "Agendas": [{"Description": "Call to Order"}, ...]}
    """

    CalendarCategoryID: int | None = None
    CalendarID: int | None = None
    CalendarCategoryTypeID: int | None = None
    CategoryCode: str | None = None  # e.g. "Order", "RCRES", "CSGEN"
    CategoryType: str | None = None  # e.g. "Order", "Regular Calendar"
    Description: str | None = None  # e.g. "Senate Bill in Committee"
    PluralDescription: str | None = None
    Sequence: int | None = None
    DisplayType: bool = False
    IsLegislationCategory: bool = False  # True if this section contains bills
    IsPrint: bool = False
    Agendas: list[Agenda] = []


class Staff(LISModel):
    """A staff member assigned to a committee or calendar.

    Example::

        {"StaffID": 1964, "AffiliationID": 23,
         "FullName": "Lehman, Hobie", "Sequence": 1,
         "IsPublic": true}
    """

    StaffID: int
    AffiliationID: int | None = None  # CommitteeID
    StaffRoleTypeID: int | None = None
    IdentityID: int | None = None
    FullName: str | None = None
    Sequence: int | None = None
    IsPublic: bool = True
    EffectiveDate: datetime | None = None
    ModificationDate: datetime | None = None


class CalendarDetail(LISModel):
    """Full calendar detail from ``/Calendar/api/getcalendarsbyidasync``.

    Includes the complete agenda with categories, legislation, votes, staff.
    Used for House floor calendars and both chambers' session calendars.

    Key: ``CalendarID``.

    Example (truncated)::

        {"CalendarID": 21146, "ReferenceNumber": "HC10314",
         "CalendarDate": "2026-03-14T10:00:00",
         "CalendarType": "Chamber", "ChamberCode": "H",
         "CalendarCategories": [
           {"CategoryCode": "Order", "Agendas": [...]},
           {"CategoryCode": "RCRES", "Agendas": [{bills with votes}]}
         ]}
    """

    CalendarID: int
    ReferenceNumber: str | None = None
    SessionCode: str | None = None
    CalendarDate: datetime | None = None
    MeetingTime: str | None = None
    CalendarNumber: int | None = None
    Description: str | None = None
    IsPublic: bool = True
    CalendarType: str | None = None
    CalendarTypeID: int | None = None
    ChamberCode: str | None = None
    SessionID: int | None = None
    VoteRoomID: int | None = None
    RoomDescription: str | None = None
    Comments: str | None = None
    PendingChange: bool = False
    IsProforma: bool = False
    DeletionDate: datetime | None = None
    staff: list[Staff] = Field(default=[], alias="Staff")
    CalendarCategories: list[CalendarCategory] = []
    CalendarComments: list[CalendarComment] = []
    CalendarFiles: list[CalendarFile] = []

    @property
    def agendas(self) -> list[Agenda]:
        """Every agenda entry across the categories, in calendar order."""
        return [a for c in self.CalendarCategories for a in c.Agendas]

    @property
    def bills(self) -> list[Agenda]:
        """The agenda entries that name a bill, in calendar order.

        Order-of-business entries (call to order, invocation) carry no
        ``LegislationNumber`` and are left out.
        """
        return [a for a in self.agendas if a.LegislationNumber]


class CalendarType(LISModel):
    """A row of ``/Calendar/api/getcalendartypesreferenceasync``: 1 Chamber, 2 Committee."""

    CalendarTypeID: int
    Name: str


class CalendarCategoryType(LISModel):
    """A row of ``/Calendar/api/getcalendarcategorytypesreferenceasync``.

    The vocabulary behind ``CategoryCode`` on calendar and docket
    categories: 98 rows, 44 House and 54 Senate, e.g. ``"CSGEN"`` = Senate
    Bill in Committee, ``"HFIC"`` = For Immediate Consideration.

    Example::

        {"CalendarCategoryTypeID": 70, "CategoryCode": "BOX1",
         "Description": "Comments", "PluralDescription": "Comments",
         "CategoryType": "Miscellaneous", "ChamberCode": "S",
         "CalendarTypeID": 1, "CategoryTypeID": 4,
         "IsLegislationCategory": false, "IsPrint": true}
    """

    CalendarCategoryTypeID: int
    CategoryCode: str | None = None
    Description: str | None = None
    PluralDescription: str | None = None
    CategoryType: str | None = None
    CategoryTypeID: int | None = None
    CalendarTypeID: int | None = None  # 1 Chamber, 2 Committee
    ChamberCode: str | None = None
    LegislationTypeCode: str | None = None
    LegislationChamberCode: str | None = None
    Sequence: int | None = None
    IsLegislationCategory: bool = False
    IsPrint: bool = False
    DisplayType: bool = False
    OnDemand: bool = False
    ClerksCopy: bool = False


class CategoryType(LISModel):
    """The 8 coarse category types, from ``getcategorytypesreferenceasync``.

    The vocabulary behind ``CategoryTypeID`` on
    :class:`~va_lis_client.models.minutes.MinutesCategory` and on
    :class:`CalendarCategoryType`.  Not to be confused with
    :class:`CalendarCategoryType`, which is the 98-row *code* vocabulary.

    Example::

        {"CategoryTypeID": 1, "Name": "Order"}
        {"CategoryTypeID": 3, "Name": "Uncontested Calendar"}
    """

    CategoryTypeID: int
    Name: str | None = None  # e.g. "Order", "Regular Calendar"


class CalendarAction(LISModel):
    """The action vocabulary behind a calendar, docket, or minutes activity.

    This is what resolves
    :attr:`~va_lis_client.models.minutes.MinutesActivity.CalendarActionID`.
    Checked against Senate minutes book 3861 on 2026-09-15: 204 of its 205
    activities resolved here, and the ``EventCode`` agreed on all 204.

    **4,952 rows hold only 490 distinct ``CalendarActionID``.**  The repeats
    are one row per calendar category the action can appear under, so
    ``CalendarActionID`` 1016 arrives 84 times, identical except for
    :attr:`CategoryCode` and :attr:`CalendarCategoryTypeID`, all 84 carrying
    ``EventCode`` ``"H4000"``.  Index it as id to *list of rows*, the same
    way :meth:`~va_lis_client.service.LISService.event_types_by_code` handles
    the repeated event codes.

    :attr:`Description` is the clean label.  A minutes activity's own
    ``ActionDescription`` is the filled-in version of it, so action 3675
    reads ``"Call to Order"`` here and ``"Called to Order at 12 m. by Lt.
    Governor Ghazala F. Hashmi"`` in the book.

    Example::

        {"CalendarActionID": 2, "EventCode": "C6019",
         "Description": "Conferees cannot agree",
         "JournalDescription": "Conferees cannot agree ",
         "IsPassed": true, "CategoryCode": "HOUBRC",
         "CalendarCategoryTypeID": 5, "LegislationChamberCode": "H",
         "ActionReferences": []}
    """

    CalendarActionID: int  # NOT unique across rows — see the class docstring
    EventCode: str | None = None  # joins the event vocabulary, e.g. "H4000"
    Description: str | None = None  # the clean label
    JournalDescription: str | None = None  # LIS pads this; LISModel strips it
    VoteDescription: str | None = None
    CategoryCode: str | None = None  # why the row repeats
    CalendarCategoryTypeID: int | None = None  # why the row repeats
    ChamberCode: str | None = None  # null in practice
    LegislationChamberCode: str | None = None
    IsPassed: bool | None = None
    InPreview: bool | None = None
    ActionReferences: list[dict] = []
